import dataclasses
import json

import pytest

from conftest import H, T0, make_book, make_candles
from reflexbot.backtest import (LookaheadGuardError, assert_after_cutoff, buy_and_hold, run_backtest,
                                walk_forward)
from reflexbot.config import Settings
from reflexbot.exchange.paper import CostModel, PaperBroker
from reflexbot.gate import Policy
from reflexbot.journal import Journal
from reflexbot.nightly import ReplayReflex, candidates, nightly_review, within_bounds
from reflexbot.policy_store import PROMOTE_PHRASE, PolicyStore
from reflexbot.reflex.rules import RuleParams, RuleReflex
from reflexbot.risk import RiskLimits
from reflexbot.runner import Runner
from reflexbot.types import Candle, ReflexDecision


def _dec(direction="long", p=0.9, q=3, regime="trending", conf=0.85):
    return ReflexDecision(regime=regime, direction=direction, direction_confidence=p, reflex_confidence=conf,
                          toxic_flow=None, setup_quality=q, risk_state="safe", model="rules-test")


class Always:
    name = "rules"

    def __init__(self, d):
        self.d = d
        self.seen = []

    def decide(self, snap):
        self.seen.append(snap)
        return self.d.model_copy()


def _data(n=400, **kw):
    return {"BTC/USDT": make_candles(n, seed=1, **kw), "ETH/USDT": make_candles(n, seed=2, start=50, **kw)}


# ---------------- backtest ----------------

def test_orders_fill_at_next_bar_open_with_costs():
    data = {"BTC/USDT": make_candles(200, drift=0.001)}
    r = run_backtest(data, "1h", Always(_dec()), Policy(max_hold_bars=5), 100, 130)
    first = r.trades[0]
    bar = next(c for c in data["BTC/USDT"] if c.ts == first["entry_ts"])
    assert first["entry_ts"] == data["BTC/USDT"][101].ts        # decided on bar 100, filled on bar 101
    assert first["entry"] > bar.o                                # slippage against us
    assert r.fees > 0


def test_no_lookahead_future_bars_do_not_change_past_decisions():
    base = make_candles(300, seed=5)
    alt = base[:200] + make_candles(100, seed=77, start=base[199].c * 3, t0=base[200].ts)
    seen_a, seen_b = Always(_dec("neutral", 0.5, 0)), Always(_dec("neutral", 0.5, 0))
    run_backtest({"BTC/USDT": base}, "1h", seen_a, Policy(), 100, 200)
    run_backtest({"BTC/USDT": alt}, "1h", seen_b, Policy(), 100, 200)
    assert seen_a.seen == seen_b.seen and len(seen_a.seen) == 99
    assert max(s["t"] for s in seen_a.seen) * 1000 <= base[199].ts   # no snapshot beyond the last decision bar


def test_stop_is_assumed_first_when_bar_touches_both():
    c = make_candles(160, vol=0.0005, drift=0.0)
    wild = c[121]
    c[121] = Candle(wild.ts, wild.o, wild.o * 1.5, wild.o * 0.5, wild.c, wild.v)
    r = run_backtest({"BTC/USDT": c}, "1h", Always(_dec()), Policy(max_hold_bars=500), 120, 125)
    assert r.trades[0]["why"] == "stop" and r.trades[0]["pnl"] < 0


def test_backtest_respects_position_and_risk_limits():
    data = {f"S{i}/USDT": make_candles(200, seed=i, drift=0.0) for i in range(5)}
    data["BTC/USDT"] = make_candles(200, seed=9)
    r = run_backtest(data, "1h", Always(_dec()), Policy(max_hold_bars=1000, stop_atr=50, target_atr=75), 100, 104)
    # 6 symbols all say buy on the same bar; only 3 slots exist and cash is shared
    assert len(r.trades) == 3 and len({t["symbol"] for t in r.trades}) == 3
    assert sum(t["qty"] * t["entry"] for t in r.trades) <= 10_000


def test_model_backtests_blocked_before_training_cutoff():
    old = T0 - 400 * 86_400_000            # well before June 2026
    with pytest.raises(LookaheadGuardError):
        assert_after_cutoff("jev", old)
    with pytest.raises(LookaheadGuardError):
        assert_after_cutoff("haiku", old)
    with pytest.raises(LookaheadGuardError):
        assert_after_cutoff("some-new-model", T0)
    assert_after_cutoff("rules", old)      # code rules may use any dates
    replay = ReplayReflex("jev", {})
    with pytest.raises(LookaheadGuardError):
        run_backtest({"BTC/USDT": make_candles(200, t0=old)}, "1h", replay, Policy(), 100, 150)


def test_benchmark_pays_costs_and_headline_states_verdict_first():
    data = _data(420, drift=0.0015)
    hold = buy_and_hold(data["BTC/USDT"], 100, 400, CostModel(), 10_000)
    gross = data["BTC/USDT"][399].c / data["BTC/USDT"][100].o - 1
    assert hold.total_return < gross
    rep = walk_forward(data, "1h", lambda rp: RuleReflex(RuleParams(**rp)), Policy(),
                       {"min_direction_confidence": [0.6, 0.8]}, train_bars=120, test_bars=60)
    assert rep.headline().startswith(("BEATS", "DOES NOT BEAT", "NO TRADES"))
    assert len(rep.folds) == 4 and all(f["test"][0] == f["train"][1] for f in rep.folds)   # test always after train
    assert len(rep.strategy.equity_curve) == 240


def test_always_flat_strategy_equals_initial_equity():
    r = run_backtest(_data(200), "1h", Always(_dec("neutral", 0.4, 0)), Policy(), 100, 190)
    assert r.total_return == 0.0 and r.trades == [] and r.max_drawdown == 0.0


def test_kill_switch_flattens_in_backtest():
    c = make_candles(140, vol=0.0005, drift=0.0)
    crash = [Candle(x.ts, x.o * 0.5, x.h * 0.5, x.l * 0.5, x.c * 0.5, x.v) for x in c[110:]]
    data = {"BTC/USDT": c[:110] + crash}
    lim = RiskLimits(kill_drawdown=0.01, risk_per_trade=0.05)
    r = run_backtest(data, "1h", Always(_dec()), Policy(stop_atr=1200, target_atr=4000, max_hold_bars=999),
                     100, 130, limits=lim)
    assert len(r.trades) == 1 and r.trades[0]["why"] == "kill_switch"    # flattened once, never re-entered


# ---------------- policy store ----------------

def test_policy_versions_promote_needs_human_and_rollback(tmp_path):
    st = PolicyStore(str(tmp_path))
    st.save(Policy(version="v1"))
    st.promote("v1", "owner", PROMOTE_PHRASE, bootstrap=True)
    st.save(Policy(version="v2", min_direction_confidence=0.85), status="proposed")
    with pytest.raises(PermissionError):
        st.promote("v2", "owner", PROMOTE_PHRASE)            # has not passed the gates
    with pytest.raises(ValueError):
        st.set_status("v2", "active")                        # no back door
    st.set_status("v2", "gates_passed")
    with pytest.raises(PermissionError):
        st.promote("v2", "", PROMOTE_PHRASE)
    with pytest.raises(PermissionError):
        st.promote("v2", "owner", "yes")
    assert st.active() == "v1"
    st.promote("v2", "owner", PROMOTE_PHRASE)
    assert st.active() == "v2" and st.load()[0].min_direction_confidence == 0.85
    assert st.rollback("owner") == "v1" and st.active() == "v1"
    assert st.load_doc("v2")["status"] == "retired"


def test_policy_file_cannot_carry_risk_limits(tmp_path):
    st = PolicyStore(str(tmp_path))
    st.save(Policy(version="v1"))
    doc = st.load_doc("v1")
    doc["policy"]["risk_per_trade"] = 0.5
    with open(tmp_path / "policy_v1.json", "w") as f:
        json.dump(doc, f)
    with pytest.raises(ValueError):
        st.load("v1")
    assert "risk_per_trade" not in {f.name for f in dataclasses.fields(Policy)}


def test_nightly_candidates_stay_in_bounds():
    for c in candidates(Policy(min_direction_confidence=0.95)):
        assert within_bounds(c) and c.kelly_fraction <= 0.25


# ---------------- nightly ----------------

def _journal_with(decisions, candles, policy="v1", model="rules-test"):
    j = Journal()
    for i, d in decisions:
        c = candles[i]
        j.decision(c.ts + H, "BTC/USDT", policy, model, {"sym": "BTC/USDT", "t": (c.ts + H) // 1000},
                   d.model_dump(), d.direction_confidence, "hold", [], 150.0, c.c)
    return j


def test_nightly_with_little_data_proposes_nothing(tmp_path):
    st = PolicyStore(str(tmp_path / "p"))
    st.save(Policy(version="v1")); st.promote("v1", "owner", PROMOTE_PHRASE, bootstrap=True)
    c = make_candles(200)
    j = _journal_with([(i, _dec()) for i in range(100, 130)], c)
    rep = nightly_review(j, st, {"BTC/USDT": c}, "1h", c[-1].ts + H, out_dir=str(tmp_path / "n"), run_tests=False)
    assert rep["scored_decisions"] == 30 and rep["brier_raw"] is not None
    assert isinstance(rep["proposal"], str) and rep["proposal"].startswith("none")
    assert st.versions() == ["v1"] and st.active() == "v1"


def test_nightly_proposes_versions_and_never_promotes(tmp_path):
    st = PolicyStore(str(tmp_path / "p"))
    st.save(Policy(version="v1")); st.promote("v1", "owner", PROMOTE_PHRASE, bootstrap=True)
    c = make_candles(400, drift=0.0, seed=4)
    j = _journal_with([(i, _dec(p=0.9)) for i in range(100, 380)], c)      # overconfident: ~50% hit rate
    j.miss(c[-1].ts, "BTC/USDT", "reflex_miss", "deadline", 2500)
    rep = nightly_review(j, st, {"BTC/USDT": c}, "1h", c[-1].ts + H, out_dir=str(tmp_path / "n"), run_tests=False)
    assert rep["scored_decisions"] >= 250
    assert rep["brier_test_new"] <= rep["brier_test_old"]                  # recalibration helps out of sample
    prop = rep["proposal"]
    assert prop["version"] == "v2" and prop["status"] in ("paper", "gates_failed")
    assert st.active() == "v1" and rep["promoted"] is False                # still the old policy
    assert st.load_doc("v2")["parent"] == "v1" and st.load_doc("v2")["status"] != "active"
    assert rep["last_24h"]["misses"] == {"reflex_miss": 1}
    assert (tmp_path / "n" / f"{rep['date']}.json").exists()


# ---------------- runner (integration, fake feeds) ----------------

class FakeData:
    def __init__(self, candles, now_ms, skew=0, book_age=0, fail=False):
        self.c, self.now, self.skew, self.book_age, self.fail = candles, now_ms, skew, book_age, fail

    def candles(self, symbol, tf, limit=240):
        if self.fail:
            raise TimeoutError("exchange down")
        return self.c[symbol][-limit:]

    def order_book(self, symbol, depth=25):
        return make_book(self.c[symbol][-1].c, depth_qty=1e6, ts=self.now - self.book_age)

    def server_time_ms(self):
        import time
        return int(time.time() * 1000) + self.skew


def _runner(tmp_path, reflex, **fake_kw):
    import time
    now = int(time.time() * 1000)
    t0 = now - (now % H) - 300 * H
    data = {"BTC/USDT": make_candles(300, t0=t0, drift=0.0005)}
    s = Settings(symbols=["BTC/USDT"], data_dir=str(tmp_path / "d"), policy_dir=str(tmp_path / "p"),
                 schema_dir=str(tmp_path / "s"))
    st = PolicyStore(s.policy_dir)
    st.save(Policy(version="v1")); st.promote("v1", "owner", PROMOTE_PHRASE, bootstrap=True)
    sent = []

    class A:
        def send(self, text, key=None, level="info"):
            sent.append(text)

    r = Runner(s, data=FakeData(data, now, **fake_kw), reflex=reflex, journal=Journal(), alerter=A())
    return r, sent


def test_runner_healthy_cycle_places_one_paper_order(tmp_path):
    r, sent = _runner(tmp_path, Always(_dec()))
    stats = r.cycle()
    assert stats == {"decided": 1, "missed": 0, "orders": 1, "unhealthy": 0}
    pos = r.broker.positions["BTC/USDT"]
    assert pos.stop < pos.entry < pos.target
    assert (pos.entry - pos.stop) * pos.qty <= 0.02 * 10_000 * 1.01
    assert len(r.journal.rows("fills")) == 1 and r.reconcile() == []
    assert r.cycle()["decided"] == 0                       # same candle is never decided twice
    assert (tmp_path / "d" / "heartbeat.json").exists()
    assert any("PAPER BUY" in s for s in sent)


def test_runner_blocks_orders_on_stale_book_clock_skew_and_exchange_error(tmp_path):
    for i, kw in enumerate(({"book_age": 120_000}, {"skew": 30_000}, {"fail": True})):
        r, sent = _runner(tmp_path / str(i), Always(_dec()), **kw)
        stats = r.cycle()
        assert stats["orders"] == 0 and stats["unhealthy"] == 1 and not r.broker.positions
        assert r.journal.rows("misses")[0]["kind"] == "unhealthy_data"


def test_runner_skips_candle_on_reflex_miss(tmp_path):
    class Down:
        name = "jev"

        def decide(self, s):
            raise ConnectionError("unreachable")

    r, _ = _runner(tmp_path, Down())
    stats = r.cycle()
    assert stats["missed"] == 1 and stats["orders"] == 0
    assert r.journal.rows("misses")[0]["kind"] == "reflex_miss"


def test_runner_pauses_and_escalates_on_low_confidence_and_crisis(tmp_path):
    r, sent = _runner(tmp_path / "a", Always(_dec(conf=0.4)))
    assert r.cycle()["orders"] == 0 and any("PAUSED" in s for s in sent)
    r, sent = _runner(tmp_path / "b", Always(_dec(regime="crisis")))
    assert r.cycle()["orders"] == 0 and any("PAUSED" in s for s in sent)


def test_runner_kill_switch_flattens_and_blocks(tmp_path):
    r, sent = _runner(tmp_path, Always(_dec()))
    r.cycle()
    r.broker.peak_equity = 20_000                           # simulate a 50% drawdown
    r.last_decided.clear()
    stats = r.cycle()
    assert r.risk.killed and not r.broker.positions
    assert any("KILL SWITCH" in s for s in sent)
    r.last_decided.clear()
    assert r.cycle()["orders"] == 0                         # stays blocked


def test_runner_halts_new_risk_on_ledger_mismatch(tmp_path):
    r, sent = _runner(tmp_path, Always(_dec()))
    r.broker.positions["ETH/USDT"] = r.broker.positions.get("ETH/USDT") or \
        __import__("reflexbot.types", fromlist=["Position"]).Position("ETH/USDT", 1.0, 50, 1, 1e9, r.now_ms())
    stats = r.cycle()
    assert stats["orders"] == 0 and "reconciliation_mismatch" in r.halted_reason
    assert any("ledger mismatch" in s for s in sent)


def test_slow_feeds_do_not_look_like_clock_skew(tmp_path):
    """Regression: time spent fetching feeds must not be counted as exchange clock skew."""
    import time as _t

    r, _ = _runner(tmp_path, Always(_dec()))
    real = r.data.candles

    def slow(symbol, tf, limit=240):
        _t.sleep(2.5)                      # longer than max_clock_skew_ms
        return real(symbol, tf, limit)

    r.data.candles = slow
    stats = r.cycle()
    assert stats["unhealthy"] == 0 and stats["decided"] == 1


def test_headline_never_calls_a_flat_strategy_a_win():
    data = _data(420, drift=-0.002)                        # falling market
    rep = walk_forward(data, "1h", lambda rp: Always(_dec("neutral", 0.4, 0)), Policy(), {}, 120, 60)
    assert rep.strategy.total_return > rep.benchmark.total_return
    assert rep.headline().startswith("NO TRADES") and "not evidence of an edge" in rep.headline()
