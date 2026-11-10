"""Kraken spot through ccxt.

KrakenData: public market data only (no keys).
KrakenLive: real orders. It refuses to construct unless the live gate is open
(see live_gate.py): explicit owner approval file + keys present in the
environment. Keys are trading-only, withdrawals off, IP-whitelisted, and live
outside the repo. They are never printed or logged.

Note on "testnet first": Kraken has no spot testnet (ccxt's sandbox mode is not
available for kraken spot). The substitutes are (1) the paper broker on live
public data and (2) Kraken's `validate=true` order flag, which checks an order
without submitting it. Both run before any live order.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from ..live_gate import require_live_approval
from ..types import Candle, OrderBook


def _ccxt_kraken(extra: Optional[dict] = None):
    import ccxt

    opts: dict = {"enableRateLimit": True, "timeout": 15000}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        opts["httpsProxy"] = proxy
    opts.update(extra or {})
    return ccxt.kraken(opts)


class KrakenData:
    def __init__(self, client=None):
        self.x = client or _ccxt_kraken()
        self._markets: Optional[dict] = None

    def markets(self) -> dict:
        if self._markets is None:
            self._markets = self.x.load_markets()
        return self._markets

    def candles(self, symbol: str, tf: str, limit: int = 720) -> list[Candle]:
        rows = self.x.fetch_ohlcv(symbol, tf, limit=limit)
        return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]

    def order_book(self, symbol: str, depth: int = 25) -> OrderBook:
        ob = self.x.fetch_order_book(symbol, depth)
        # Kraken gives per-level timestamps only; stamp with local receive time.
        return OrderBook([(float(p), float(q)) for p, q, *_ in ob["bids"]],
                         [(float(p), float(q)) for p, q, *_ in ob["asks"]],
                         int(time.time() * 1000))

    def server_time_ms(self) -> int:
        return int(self.x.fetch_time())

    def min_order(self, symbol: str) -> tuple[float, float]:
        m = self.markets()[symbol]
        return float(m["limits"]["amount"]["min"] or 0.0), float(m["limits"]["cost"]["min"] or 0.0)


class KrakenLive:
    """Real-money adapter. Constructing it without owner approval raises LiveNotApproved."""

    def __init__(self):
        require_live_approval()
        self.x = _ccxt_kraken({
            "apiKey": os.environ["KRAKEN_API_KEY"],
            "secret": os.environ["KRAKEN_API_SECRET"],
        })

    def validate_order(self, symbol: str, side: str, qty: float) -> dict:
        """Ask Kraken to validate the order without placing it."""
        return self.x.create_order(symbol, "market", side, qty, None, {"validate": True})

    def market_order(self, symbol: str, side: str, qty: float) -> dict:
        return self.x.create_order(symbol, "market", side, qty)

    def balances(self) -> dict[str, float]:
        bal = self.x.fetch_balance()
        return {k: float(v) for k, v in bal.get("total", {}).items() if v}
