"""Backtester: event-driven, walk-forward only, fees and slippage always on.

Timing (no look-ahead):
  - The decision for bar i uses candles[0..i] only (all closed).
  - Any resulting order fills at the OPEN of bar i+1, plus slippage and fees.
  - Stops/targets are checked against bar i+1.. highs and lows. If a bar touches
    both stop and target, the stop is assumed to have hit first.
Benchmark: buy BTC at the first test bar's open and hold, paying the same fee
and slippage on entry and on the final mark. Every report leads with whether the
strategy beat it.

Model guard: a decision model may only be replayed on candles after its
training cutoff (see reflex.base.MODEL_CUTOFFS). Earlier data raises.
"""
from __future__ import annotations

import datetime as dt
import itertools
import math
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .calibration import Calibrator, brier_score, direction_outcome
from .exchange.paper import CostModel, OrderRejected, PaperBroker
from .gate import Policy, gate, position_size
from .reflex.base import MODEL_CUTOFFS, Reflex
from .risk import RiskEngine, RiskLimits
from .state_engine import MIN_BARS, TF_MS, atr, build_snapshot
from .types import Candle, OrderIntent, Position


class LookaheadGuardError(ValueError):
    pass


def assert_after_cutoff(reflex_name: str, first_decision_ms: int) -> None:
    cutoff = MODEL_CUTOFFS.get(reflex_name)
    if reflex_name not in MODEL_CUTOFFS:
        raise LookaheadGuardError(f"no training cutoff registered for model '{reflex_name}'")
    if cutoff is None:
        return
    cutoff_ms = int(dt.datetime.fromisoformat(cutoff).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 86_400_000
    if first_decision_ms < cutoff_ms:
        raise LookaheadGuardError(
            f"model '{reflex_name}' may only be backtested after {cutoff}; "
            f"got a decision at {dt.datetime.fromtimestamp(first_decision_ms / 1000, dt.timezone.utc):%Y-%m-%d}"
        )


@dataclass
class Result:
    start_ms: int
    end_ms: int
    equity_curve: list[float]
    trades: list[dict]
    decisions: list[dict] = field(default_factory=list)
    misses: int = 0
    rejected: int = 0
    fees: float = 0.0
    initial: float = 0.0
    bars_per_year: float = 8760.0

    @property
    def total_return(self) -> float:
        return self.equity_curve[-1] / self.initial - 1 if self.equity_curve else 0.0

    @property
    def max_drawdown(self) -> float:
        peak, dd = -math.inf, 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            dd = max(dd, 1 - e / peak)
        return dd

    @property
    def sharpe(self) -> float:
        r = [b / a - 1 for a, b in zip(self.equity_curve, self.equity_curve[1:])]
        if len(r) < 2:
            return 0.0
        m = sum(r) / len(r)
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))
        return m / sd * math.sqrt(self.bars_per_year) if sd > 0 else 0.0

    @property
    def win_rate(self) -> float:
        return sum(1 for t in self.trades if t["pnl"] > 0) / len(self.trades) if self.trades else 0.0

    def summary(self) -> dict:
        return {"return_pct": round(self.total_return * 100, 2),
                "max_dd_pct": round(self.max_drawdown * 100, 2),
                "sharpe": round(self.sharpe, 2), "trades": len(self.trades),
                "win_rate_pct": round(self.win_rate * 100, 1), "fees": round(self.fees, 2),
                "misses": self.misses, "rejected": self.rejected}


def buy_and_hold(candles: list[Candle], start_i: int, end_i: int, cost: CostModel, initial: float) -> Result:
    """Hold BTC (or whatever `candles` is) from open of bar start_i to close of bar end_i-1, after costs."""
    slip = cost.base_slippage_bps / 1e4
    entry = candles[start_i].o * (1 + slip)
    qty = initial / (entry * (1 + cost.taker_fee))
    curve = [qty * c.c for c in candles[start_i:end_i]]
    curve[-1] = qty * candles[end_i - 1].c * (1 - slip) * (1 - cost.taker_fee)
    return Result(candles[start_i].ts, candles[end_i - 1].ts, curve, [], initial=initial)


def run_backtest(
    data: dict[str, list[Candle]],
    tf: str,
    reflex: Reflex,
    policy: Policy,
    start_i: int,
    end_i: int,
    initial: float = 10_000.0,
    limits: RiskLimits = RiskLimits(),
    cost: CostModel = CostModel(),
    calibrator: Optional[Calibrator] = None,
    theses: Optional[dict[str, dict]] = None,
    horizon: int = 12,
    off_limits: Optional[set[str]] = None,
    latency_ms_fn: Optional[Callable[[], float]] = None,
) -> Result:
    """Replay bars [start_i, end_i) on aligned candle lists. Decisions at bar i trade at bar i+1 open."""
    tf_ms = TF_MS[tf]
    symbols = list(data)
    n = min(len(c) for c in data.values())
    end_i = min(end_i, n)
    if start_i < MIN_BARS:
        raise ValueError(f"start_i must be >= {MIN_BARS} so every snapshot has history")
    ref = data[symbols[0]]
    for s in symbols:
        if [c.ts for c in data[s][start_i:end_i]] != [c.ts for c in ref[start_i:end_i]]:
            raise ValueError(f"candles for {s} are not aligned with {symbols[0]}")
    assert_after_cutoff(reflex.name, ref[start_i].ts + tf_ms)

    cal = calibrator or Calibrator()
    broker = PaperBroker(initial, cost)
    risk = RiskEngine(limits, off_limits=off_limits, allowed=set(symbols))
    res = Result(ref[start_i].ts, ref[end_i - 1].ts, [], [], initial=initial,
                 bars_per_year=365 * 86_400_000 / tf_ms)
    pending: list[tuple[str, OrderIntent]] = []   # orders decided at bar i-1, to fill at open of bar i
    open_meta: dict[str, dict] = {}

    def close_position(sym: str, ts: int, ref_px: float, vol_quote: float, why: str) -> None:
        pos = broker.positions[sym]
        try:
            f = broker.market_order(sym, "sell", pos.qty, ts, ref_px, bar_quote_volume=vol_quote)
        except OrderRejected:
            res.rejected += 1
            return
        if sym in broker.positions:   # partial exit (liquidity cap): keep managing the rest
            return
        m = open_meta.pop(sym)
        res.trades.append({"symbol": sym, "entry_ts": m["ts"], "exit_ts": ts, "entry": m["entry"],
                           "exit": f.price, "qty": m["qty"], "why": why,
                           "pnl": (f.price - m["entry"]) * m["qty"] - f.fee - m["fee"]})

    for i in range(start_i, end_i):
        bar_ts = ref[i].ts
        day = dt.datetime.fromtimestamp(bar_ts / 1000, dt.timezone.utc).strftime("%Y-%m-%d")

        # 1) fill orders decided on the previous bar, at this bar's open
        for sym, intent in pending:
            c = data[sym][i]
            volq = c.v * c.o
            try:
                if intent.side == "buy":
                    scale = c.o / intent.ref_price          # re-anchor stop/target to the real entry
                    f = broker.market_order(sym, "buy", intent.qty, bar_ts, c.o, bar_quote_volume=volq,
                                            stop=intent.stop * scale, target=intent.target * scale)
                    open_meta[sym] = {"ts": bar_ts, "entry": f.price, "qty": f.qty, "fee": f.fee, "bar": i}
                elif sym in broker.positions:
                    close_position(sym, bar_ts, c.o, volq, intent.reason)
            except OrderRejected:
                res.rejected += 1
        pending = []

        # 2) stops, targets, time exits inside this bar (stop first if both touched)
        for sym in list(broker.positions):
            c, pos = data[sym][i], broker.positions[sym]
            volq = c.v * c.c
            if c.l <= pos.stop:
                close_position(sym, bar_ts, min(pos.stop, c.o), volq, "stop")
            elif c.h >= pos.target:
                close_position(sym, bar_ts, max(pos.target, c.o), volq, "target")
            elif i - open_meta[sym]["bar"] >= policy.max_hold_bars:
                close_position(sym, bar_ts, c.c, volq, "time")

        # 3) mark to market at this bar's close, then decide for the next bar
        prices = {s: data[s][i].c for s in symbols}
        account = broker.mark(prices, day)
        risk.update(account)
        res.equity_curve.append(account.equity)
        if risk.killed:
            for sym in list(broker.positions):
                pending.append((sym, OrderIntent(sym, "sell", broker.positions[sym].qty, prices[sym], True, reason="kill_switch")))
            continue
        if i == end_i - 1:
            break

        as_of = bar_ts + tf_ms
        for sym in symbols:
            hist = data[sym][max(0, i + 1 - 240): i + 1]
            thesis = (theses or {}).get(sym)
            snap = build_snapshot(sym, tf, hist, as_of, account, None, thesis)
            if latency_ms_fn is not None and latency_ms_fn() > policy.deadline_ms:
                res.misses += 1
                continue
            try:
                d = reflex.decide(snap)
            except Exception:
                res.misses += 1
                continue
            p_cal = cal(d.direction_confidence)
            thesis_ok = snap.get("thesis", {}).get("above_invalidation") if thesis else True
            g = gate(d, p_cal, risk.risk_state(account), policy, sym in broker.positions,
                     thesis_valid=thesis_ok if thesis_ok is not None else True)
            fwd = data[sym][i + horizon].c if i + horizon < n else None
            res.decisions.append({"ts": as_of, "symbol": sym, "direction": d.direction,
                                  "p": d.direction_confidence, "p_cal": p_cal, "action": g.action,
                                  "outcome": None if fwd is None else direction_outcome(d.direction, prices[sym], fwd),
                                  "in_test": i + horizon < end_i})
            if g.action == "exit":
                pos = broker.positions[sym]
                pending.append((sym, OrderIntent(sym, "sell", pos.qty, prices[sym], True, reason=g.reasons[0])))
            elif g.action == "enter_long":
                a = atr(hist)
                qty, stop, target = position_size(account.equity, prices[sym], p_cal, a, policy, limits.risk_per_trade)
                intent = OrderIntent(sym, "buy", qty, prices[sym], False, stop, target, "entry")
                v = risk.check(intent, account, prices)
                if v.approved:
                    intent.qty = v.qty
                    pending.append((sym, intent))
                    # Reserve the slot and the cash so position-count, leverage and cash
                    # limits also hold across several entries decided on the same bar.
                    account.positions[sym] = Position(sym, v.qty, prices[sym], stop, target, as_of)
                    account.cash -= v.qty * prices[sym] * (1 + cost.taker_fee)
                else:
                    res.rejected += 1

    # final mark: liquidate at last close with costs so the comparison is fair
    last_i = end_i - 1
    for sym in list(broker.positions):
        c = data[sym][last_i]
        close_position(sym, c.ts, c.c, c.v * c.c, "end_of_test")
    final_prices = {s: data[s][last_i].c for s in symbols}
    res.equity_curve[-1] = broker.equity(final_prices)
    res.fees = broker.fees_paid
    return res


# ---------------- walk-forward ----------------

@dataclass
class WalkForwardReport:
    folds: list[dict]
    strategy: Result
    benchmark: Result
    brier: Optional[float]
    n_scored: int

    @property
    def beats_btc(self) -> bool:
        return self.strategy.total_return > self.benchmark.total_return

    def headline(self) -> str:
        s, b = self.strategy.total_return * 100, self.benchmark.total_return * 100
        n = len(self.strategy.trades)
        if n == 0:
            return (f"NO TRADES: strategy stayed flat (0.00%) while BTC hold returned {b:+.2f}%. "
                    f"Being flat is not evidence of an edge ({len(self.folds)} out-of-sample folds).")
        verdict = "BEATS" if self.beats_btc else "DOES NOT BEAT"
        note = " Too few trades to conclude anything." if n < 30 else ""
        return (f"{verdict} holding BTC after costs: strategy {s:+.2f}% vs BTC hold {b:+.2f}% "
                f"(out-of-sample, {len(self.folds)} folds, {n} trades).{note}")


def walk_forward(
    data: dict[str, list[Candle]],
    tf: str,
    make_reflex: Callable[[dict], Reflex],
    base_policy: Policy,
    grid: dict[str, list],
    train_bars: int,
    test_bars: int,
    initial: float = 10_000.0,
    limits: RiskLimits = RiskLimits(),
    cost: CostModel = CostModel(),
    btc_symbol: str = "BTC/USDT",
    theses: Optional[dict[str, dict]] = None,
    horizon: int = 12,
) -> WalkForwardReport:
    """Rolling walk-forward: pick parameters on each train window, trade the next test window.

    Only test-window results are reported. Equity carries across folds. The
    calibrator for each test fold is fitted on that fold's train decisions only.
    """
    n = min(len(c) for c in data.values())
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*[grid[k] for k in keys])] or [{}]
    policy_fields = set(Policy.__dataclass_fields__)

    def split(params: dict) -> tuple[Policy, dict]:
        pol = replace(base_policy, **{k: v for k, v in params.items() if k in policy_fields})
        return pol, {k: v for k, v in params.items() if k not in policy_fields}

    folds, curve, trades, decisions = [], [], [], []
    equity = initial
    start = MIN_BARS + train_bars
    first_test = start
    misses = rejected = 0
    fees = 0.0
    while start + test_bars <= n:
        tr0, tr1, te1 = start - train_bars, start, start + test_bars
        best, best_score, best_res = combos[0], -math.inf, None
        for params in combos:
            pol, rp = split(params)
            r = run_backtest(data, tf, make_reflex(rp), pol, tr0, tr1, initial, limits, cost,
                             theses=theses, horizon=horizon)
            score = r.total_return - 0.5 * r.max_drawdown     # reward return, penalise drawdown
            if score > best_score:
                best, best_score, best_res = params, score, r
        scored = [d for d in best_res.decisions if d["outcome"] is not None and d["in_test"]]
        cal = Calibrator().fit([d["p"] for d in scored], [d["outcome"] for d in scored]) if scored else Calibrator()
        pol, rp = split(best)
        r = run_backtest(data, tf, make_reflex(rp), pol, tr1, te1, equity, limits, cost,
                         calibrator=cal, theses=theses, horizon=horizon)
        folds.append({"train": [tr0, tr1], "test": [tr1, te1], "params": best,
                      "train_return_pct": round(best_res.total_return * 100, 2), **r.summary()})
        curve += r.equity_curve
        trades += r.trades
        decisions += r.decisions
        misses += r.misses
        rejected += r.rejected
        fees += r.fees
        equity = r.equity_curve[-1]
        start += test_bars
    if not folds:
        raise ValueError("not enough data for a single walk-forward fold")
    last_test = folds[-1]["test"][1]
    strat = Result(data[btc_symbol][first_test].ts, data[btc_symbol][last_test - 1].ts, curve, trades,
                   decisions, misses, rejected, fees, initial, 365 * 86_400_000 / TF_MS[tf])
    bench = buy_and_hold(data[btc_symbol], first_test, last_test, cost, initial)
    bench.bars_per_year = strat.bars_per_year
    scored = [d for d in decisions if d["outcome"] is not None and d["in_test"]]
    brier = brier_score([d["p_cal"] for d in scored], [d["outcome"] for d in scored]) if scored else None
    return WalkForwardReport(folds, strat, bench, brier, len(scored))
