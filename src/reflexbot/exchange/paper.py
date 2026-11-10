"""Paper broker: simulated fills with real fees, slippage, liquidity limits and failures.

Nothing here touches an exchange. Live market data (candles / order books) is
passed in by the caller; this module only does the accounting.

Fill models:
- With an order book: the order walks the book level by level (real depth).
- Without one (backtests): slippage = base_bps + impact_coef * participation,
  where participation = order notional / bar quote volume, capped at
  max_participation (larger orders are cut down, tiny bars reject).
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass
from typing import Optional

from ..types import AccountView, Fill, OrderBook, Position


class OrderRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class CostModel:
    taker_fee: float = 0.0026        # Kraken spot taker, base tier (read via ccxt 2026-11-10)
    base_slippage_bps: float = 3.0
    impact_coef_bps: float = 100.0   # extra bps at 100% participation
    max_participation: float = 0.02  # never more than 2% of a bar's volume
    min_notional: float = 5.0
    fail_rate: float = 0.0           # injected order failures (tests / chaos)


class PaperBroker:
    def __init__(self, cash: float, cost: CostModel = CostModel(),
                 state_path: Optional[str] = None, seed: int = 7):
        self.cost = cost
        self.state_path = state_path
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.peak_equity = cash
        self.day_start_equity = cash
        self.day = ""
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self._n = 0
        self._rng = random.Random(seed)
        if state_path and os.path.exists(state_path):
            self._load()

    # ----- persistence (survives restarts) -----
    def _load(self) -> None:
        with open(self.state_path) as f:
            st = json.load(f)
        self.cash = st["cash"]
        self.positions = {s: Position(**p) for s, p in st["positions"].items()}
        self.peak_equity = st["peak_equity"]
        self.day_start_equity = st["day_start_equity"]
        self.day = st.get("day", "")
        self.realized_pnl = st.get("realized_pnl", 0.0)
        self.fees_paid = st.get("fees_paid", 0.0)
        self._n = st.get("n", 0)

    def save(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "cash": self.cash,
                "positions": {s: asdict(p) for s, p in self.positions.items()},
                "peak_equity": self.peak_equity, "day_start_equity": self.day_start_equity,
                "day": self.day, "realized_pnl": self.realized_pnl,
                "fees_paid": self.fees_paid, "n": self._n,
            }, f)
        os.replace(tmp, self.state_path)

    # ----- accounting -----
    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(s, p.entry) for s, p in self.positions.items())

    def mark(self, prices: dict[str, float], day: str) -> AccountView:
        """Mark to market, roll the UTC day, update the equity peak."""
        eq = self.equity(prices)
        if day != self.day:
            self.day = day
            self.day_start_equity = eq
        self.peak_equity = max(self.peak_equity, eq)
        return AccountView(equity=eq, cash=self.cash, peak_equity=self.peak_equity,
                           day_start_equity=self.day_start_equity, positions=dict(self.positions))

    # ----- fills -----
    def _price_from_book(self, side: str, qty: float, book: OrderBook) -> tuple[float, float]:
        levels = book.asks if side == "buy" else book.bids
        mid = (book.asks[0][0] + book.bids[0][0]) / 2
        left, cost = qty, 0.0
        for px, q in levels:
            take = min(left, q)
            cost += take * px
            left -= take
            if left <= 1e-12:
                break
        if left > 1e-12:
            raise OrderRejected("insufficient_liquidity:order_larger_than_visible_book")
        avg = cost / qty
        return avg, abs(avg / mid - 1) * 1e4

    def _price_from_bar(self, side: str, qty: float, ref: float, bar_quote_volume: float) -> tuple[float, float, float]:
        c = self.cost
        if bar_quote_volume <= 0:
            raise OrderRejected("insufficient_liquidity:no_volume")
        max_notional = c.max_participation * bar_quote_volume
        if qty * ref > max_notional:
            qty = max_notional / ref
        part = qty * ref / bar_quote_volume
        slip = c.base_slippage_bps + c.impact_coef_bps * part
        px = ref * (1 + slip / 1e4) if side == "buy" else ref * (1 - slip / 1e4)
        return px, slip, qty

    def market_order(self, symbol: str, side: str, qty: float, ts: int, ref_price: float,
                     book: Optional[OrderBook] = None, bar_quote_volume: Optional[float] = None,
                     stop: float = 0.0, target: float = 0.0) -> Fill:
        c = self.cost
        if self._rng.random() < c.fail_rate:
            raise OrderRejected("exchange_error:injected_failure")
        if qty <= 0:
            raise OrderRejected("invalid_qty")
        if side == "sell":
            pos = self.positions.get(symbol)
            if pos is None:
                raise OrderRejected("no_position:spot_cannot_short")
            qty = min(qty, pos.qty)
        if book is not None:
            px, slip = self._price_from_book(side, qty, book)
        elif bar_quote_volume is not None:
            px, slip, qty = self._price_from_bar(side, qty, ref_price, bar_quote_volume)
        else:
            raise OrderRejected("no_liquidity_reference")
        notional = qty * px
        if notional < c.min_notional:
            raise OrderRejected(f"below_min_notional:{notional:.2f}")
        fee = notional * c.taker_fee
        if side == "buy":
            if notional + fee > self.cash + 1e-9:
                raise OrderRejected("insufficient_funds")
            if symbol in self.positions:
                raise OrderRejected("already_in_position")
            self.cash -= notional + fee
            self.positions[symbol] = Position(symbol, qty, px, stop, target, ts, fees_paid=fee)
        else:
            pos = self.positions[symbol]
            self.cash += notional - fee
            self.realized_pnl += (px - pos.entry) * qty - fee - pos.fees_paid * (qty / pos.qty)
            if qty >= pos.qty - 1e-12:
                del self.positions[symbol]
            else:
                pos.fees_paid *= 1 - qty / pos.qty
                pos.qty -= qty
        self.fees_paid += fee
        self._n += 1
        self.save()
        return Fill(symbol, side, qty, px, fee, ts, slip, order_id=f"paper-{self._n}")


def reconcile(local: dict[str, float], exchange: dict[str, float], tol: float = 1e-6) -> list[str]:
    """Compare our ledger with what the exchange reports. Any difference halts new risk."""
    diffs = []
    for asset in sorted(set(local) | set(exchange)):
        a, b = local.get(asset, 0.0), exchange.get(asset, 0.0)
        if abs(a - b) > tol * max(1.0, abs(a), abs(b)):
            diffs.append(f"{asset}: local={a:.8f} exchange={b:.8f}")
    return diffs
