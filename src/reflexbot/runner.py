"""24/7 runtime loop (paper by default).

One cycle, per symbol, once per closed candle:
  feeds -> health check -> snapshot -> REFLEX (timed, with deadline) -> gate ->
  sizing -> hard risk check -> order -> journal -> alerts.
Between candles it keeps marking the account, enforcing stops/targets and the
kill switch, reconciling the ledger, and writing a heartbeat for the health check.

Live orders are not wired into this loop in this build: the runtime trades the
paper broker only. Going live needs the owner's approval (live_gate.py) and the
KrakenLive adapter to be switched in deliberately.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from typing import Optional

from .alerts import Alerter
from .config import Settings, make_reflex
from .exchange.paper import OrderRejected, PaperBroker, reconcile
from .gate import gate, position_size
from .journal import Journal
from .policy_store import PolicyStore
from .reflex.base import timed_decide
from .reflex.schema import load_thesis
from .risk import RiskEngine
from .state_engine import TF_MS, atr, build_snapshot, closed_candles, health_issues
from .types import OrderIntent

log = logging.getLogger("reflexbot.runner")


class Runner:
    def __init__(self, settings: Settings, data=None, reflex=None, broker=None, journal=None,
                 alerter=None, clock=time.time):
        s = self.s = settings
        os.makedirs(s.data_dir, exist_ok=True)
        self.clock = clock
        if data is None:
            from .exchange.kraken import KrakenData
            data = KrakenData()
        self.data = data
        self.store = PolicyStore(s.policy_dir)
        self.policy, self.calibrator = self.store.load()
        self.reflex = reflex or make_reflex(s.reflex_provider, s.reflex_model)
        self.broker = broker or PaperBroker(s.paper_balance, s.cost, os.path.join(s.data_dir, "paper_state.json"))
        self.journal = journal or Journal(os.path.join(s.data_dir, "journal.sqlite"))
        self.alerter = alerter or Alerter()
        self.risk = RiskEngine(s.limits, os.path.join(s.data_dir, "risk_state.json"),
                               off_limits=set(s.off_limits), allowed=set(s.symbols))
        self.tf_ms = TF_MS[s.timeframe]
        self.last_decided: dict[str, int] = {}
        self.paused: dict[str, bool] = {}
        self.halted_reason = ""
        self.heartbeat_path = os.path.join(s.data_dir, "heartbeat.json")

    # ----- helpers -----
    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _heartbeat(self, extra: Optional[dict] = None) -> None:
        tmp = self.heartbeat_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": self.now_ms(), "policy": self.policy.version, "mode": "paper",
                       "killed": self.risk.killed, "halted": self.halted_reason, **(extra or {})}, f)
        os.replace(tmp, self.heartbeat_path)

    def _ledger_from_journal(self) -> dict[str, float]:
        pos: dict[str, float] = {}
        for f in self.journal.rows("fills"):
            pos[f["symbol"]] = pos.get(f["symbol"], 0.0) + (f["qty"] if f["side"] == "buy" else -f["qty"])
        return {k: v for k, v in pos.items() if abs(v) > 1e-12}

    def reconcile(self) -> list[str]:
        """Journal-derived positions must match what the broker holds; if not, stop adding risk."""
        diffs = reconcile(self._ledger_from_journal(), {s: p.qty for s, p in self.broker.positions.items()})
        if diffs:
            self.halted_reason = "reconciliation_mismatch: " + "; ".join(diffs)
            self.journal.event("reconcile_mismatch", self.halted_reason)
            self.alerter.send("HALT new risk - ledger mismatch: " + "; ".join(diffs), key="reconcile", level="warn")
        return diffs

    def _exit(self, symbol: str, book, px: float, ts: int, why: str) -> bool:
        pos = self.broker.positions.get(symbol)
        if pos is None:
            return False
        try:
            f = self.broker.market_order(symbol, "sell", pos.qty, ts, px, book=book)
        except OrderRejected as e:
            self.journal.miss(ts, symbol, "order_failed", f"exit {why}: {e}")
            self.alerter.send(f"EXIT FAILED {symbol} ({why}): {e}", key=f"exitfail:{symbol}", level="warn")
            return False
        self.journal.fill(f, why)
        self.alerter.send(f"EXIT {symbol} {f.qty:.6f} @ {f.price:.4f} ({why})")
        return True

    # ----- one pass over all symbols -----
    def cycle(self) -> dict:
        s, now = self.s, self.now_ms()
        day = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        stats = {"decided": 0, "missed": 0, "orders": 0, "unhealthy": 0}
        feeds: dict[str, dict] = {}
        # Clock skew = exchange clock minus ours, measured once, at the moment of the request.
        try:
            skew_ms: Optional[int] = self.data.server_time_ms() - self.now_ms()
        except Exception as e:
            skew_ms = None
            log.warning("server time failed: %s", e)

        for sym in s.symbols:
            err, candles, book = None, [], None
            try:
                candles = closed_candles(self.data.candles(sym, s.timeframe, 240), self.tf_ms, now)
                book = self.data.order_book(sym)
            except Exception as e:
                err = type(e).__name__
            t = self.now_ms()
            issues = health_issues(candles, self.tf_ms, t, book, None if skew_ms is None else t + skew_ms,
                                   s.health, exchange_error=err)
            feeds[sym] = {"candles": candles, "book": book, "issues": issues}

        prices = {}
        for sym, f in feeds.items():
            b = f["book"]
            if b and b.bids and b.asks:
                prices[sym] = (b.bids[0][0] + b.asks[0][0]) / 2
            elif f["candles"]:
                prices[sym] = f["candles"][-1].c
        account = self.broker.mark(prices, day)
        self.journal.mark(now, account.equity, account.cash, {k: p.qty for k, p in account.positions.items()})

        # kill switch: latch, flatten, alert
        if self.risk.update(account):
            self.alerter.send(f"KILL SWITCH ON: {self.risk.kill_reason}. Flattening; new risk blocked until reset.",
                              key="kill", level="warn")
            for sym in list(self.broker.positions):
                if feeds.get(sym, {}).get("book"):
                    self._exit(sym, feeds[sym]["book"], prices[sym], now, "kill_switch")

        # stops and targets on the live mid
        for sym, pos in list(self.broker.positions.items()):
            px, book = prices.get(sym), feeds.get(sym, {}).get("book")
            if px is None or book is None:
                continue
            if px <= pos.stop:
                self._exit(sym, book, px, now, "stop")
            elif px >= pos.target:
                self._exit(sym, book, px, now, "target")
            elif (now - pos.opened_ts) >= self.policy.max_hold_bars * self.tf_ms:
                self._exit(sym, book, px, now, "time")

        self.reconcile()

        for sym in s.symbols:
            f = feeds[sym]
            if not f["candles"]:
                stats["unhealthy"] += 1
                self.journal.miss(now, sym, "unhealthy_data", ";".join(f["issues"]))
                continue
            candle_close = f["candles"][-1].ts + self.tf_ms
            if self.last_decided.get(sym) == candle_close:
                continue                                   # already handled this candle
            self.last_decided[sym] = candle_close
            if f["issues"]:
                stats["unhealthy"] += 1
                self.journal.miss(candle_close, sym, "unhealthy_data", ";".join(f["issues"]))
                self.alerter.send(f"DATA UNHEALTHY {sym}: {'; '.join(f['issues'])} - no orders",
                                  key=f"health:{sym}", level="warn")
                continue
            account = self.broker.mark(prices, day)
            thesis = load_thesis(s.schema_dir, sym)
            snap = build_snapshot(sym, s.timeframe, f["candles"], self.now_ms(), account, f["book"], thesis)
            tr = timed_decide(self.reflex, snap, self.policy.deadline_ms)
            if tr.missed:
                stats["missed"] += 1                       # skip this candle; never trade late
                self.journal.miss(candle_close, sym, "reflex_miss", tr.error, tr.latency_ms)
                continue
            d = tr.decision
            p_cal = self.calibrator(d.direction_confidence)
            thesis_ok = snap.get("thesis", {}).get("above_invalidation") if thesis else True
            g = gate(d, p_cal, self.risk.risk_state(account), self.policy, sym in self.broker.positions,
                     thesis_valid=True if thesis_ok is None else thesis_ok)
            stats["decided"] += 1
            self.journal.decision(candle_close, sym, self.policy.version, d.model, snap, d.model_dump(),
                                  p_cal, g.action, g.reasons, tr.latency_ms, prices[sym])
            # Escalate when a symbol enters the paused state (and when it leaves it), not every candle.
            was_paused = self.paused.get(sym, False)
            self.paused[sym] = g.pause_new_risk
            if g.pause_new_risk and not was_paused:
                self.journal.event("paused", f"{sym} regime={d.regime} reflex_confidence={d.reflex_confidence:.2f}")
                self.alerter.send(f"PAUSED new risk on {sym}: regime={d.regime}, reflex_confidence="
                                  f"{d.reflex_confidence:.2f} (< {self.policy.min_reflex_confidence} or crisis). "
                                  f"Needs your review.", level="warn")
            elif was_paused and not g.pause_new_risk:
                self.alerter.send(f"RESUMED {sym}: reflex_confidence={d.reflex_confidence:.2f}, regime={d.regime}")
            if g.action == "exit":
                stats["orders"] += self._exit(sym, f["book"], prices[sym], self.now_ms(), g.reasons[0])
            elif g.action == "enter_long" and not self.halted_reason:
                try:   # price and fill against a fresh book, not the one fetched before the decision
                    book = self.data.order_book(sym)
                    stale = health_issues(f["candles"], self.tf_ms, self.now_ms(), book,
                                          None if skew_ms is None else self.now_ms() + skew_ms, s.health)
                except Exception as e:
                    book, stale = None, [f"exchange_error:{type(e).__name__}"]
                if stale:
                    self.journal.miss(candle_close, sym, "unhealthy_data", ";".join(stale))
                    continue
                f["book"] = book
                ask = book.asks[0][0]
                qty, stop, target = position_size(account.equity, ask, p_cal, atr(f["candles"]),
                                                  self.policy, s.limits.risk_per_trade)
                intent = OrderIntent(sym, "buy", qty, ask, False, stop, target, "entry")
                v = self.risk.check(intent, account, prices, health=f["issues"])
                if not v.approved:
                    self.journal.miss(candle_close, sym, "risk_rejected", ";".join(v.reasons))
                    continue
                try:
                    fill = self.broker.market_order(sym, "buy", v.qty, self.now_ms(), ask, book=f["book"],
                                                    stop=stop, target=target)
                except OrderRejected as e:
                    self.journal.miss(candle_close, sym, "order_failed", str(e))
                    self.alerter.send(f"ORDER FAILED {sym}: {e}", key=f"orderfail:{sym}", level="warn")
                    continue
                self.journal.fill(fill, "entry")
                stats["orders"] += 1
                self.alerter.send(f"PAPER BUY {sym} {fill.qty:.6f} @ {fill.price:.4f} stop {stop:.4f} "
                                  f"target {target:.4f} p={p_cal:.2f}")
        self._heartbeat(stats)
        return stats

    def run_forever(self, poll_s: float = 60.0) -> None:
        self.alerter.send(f"reflexbot started: paper, policy {self.policy.version}, reflex {self.reflex.name}, "
                          f"symbols {','.join(self.s.symbols)}")
        while True:
            try:
                stats = self.cycle()
                if stats["decided"] or stats["missed"] or stats["orders"]:
                    log.info("cycle %s", stats)
            except Exception as e:  # the loop must survive anything; the error is recorded and alerted
                log.exception("cycle failed")
                self.journal.event("cycle_error", f"{type(e).__name__}: {e}")
                self.alerter.send(f"CYCLE ERROR: {type(e).__name__}: {e}", key="cycle_error", level="warn")
                self._heartbeat({"error": type(e).__name__})
            time.sleep(poll_s)
