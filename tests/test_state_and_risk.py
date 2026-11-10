import dataclasses

import pytest

from conftest import H, T0, flat_account, make_book, make_candles
from reflexbot.gate import MAX_KELLY_FRACTION, Policy, gate, kelly_fraction, position_size
from reflexbot.risk import RiskEngine, RiskLimits
from reflexbot.state_engine import (CausalityError, HealthConfig, build_snapshot, closed_candles,
                                    estimate_tokens, health_issues)
from reflexbot.types import AccountView, OrderIntent, Position, ReflexDecision


# ---------------- state engine ----------------

def test_snapshot_is_compact_and_numeric(candles, account):
    as_of = candles[-1].ts + H
    snap = build_snapshot("BTC/USDT", "1h", candles, as_of, account, make_book(candles[-1].c, ts=as_of),
                          {"bias": "long", "invalid_below": 50.0})
    assert estimate_tokens(snap) < 400
    assert snap["spread_bps"] == pytest.approx(4.0, abs=0.1)
    assert snap["imbalance"] == 0.0
    assert snap["thesis"]["above_invalidation"] is True
    assert snap["open_pos"] == 0 and snap["exposure_pct"] == 0.0


def test_snapshot_rejects_future_candle(candles, account):
    as_of = candles[-2].ts + H          # the last candle closes after as_of
    with pytest.raises(CausalityError):
        build_snapshot("BTC/USDT", "1h", candles, as_of, account)


def test_snapshot_rejects_future_book(candles, account):
    as_of = candles[-1].ts + H
    with pytest.raises(CausalityError):
        build_snapshot("BTC/USDT", "1h", candles, as_of, account, make_book(ts=as_of + 1))


def test_snapshot_ignores_data_after_as_of(account):
    """Changing anything after time T must not change the snapshot for T."""
    a = make_candles(300, seed=3)
    b = a[:200] + make_candles(100, seed=99, start=7.0, t0=a[200].ts)
    as_of = a[199].ts + H
    assert build_snapshot("X/USDT", "1h", a[:200], as_of, account) == build_snapshot("X/USDT", "1h", b[:200], as_of, account)


def test_closed_candles_drops_forming_bar(candles):
    now = candles[-1].ts + H // 2       # last bar still forming
    assert closed_candles(candles, H, now)[-1].ts == candles[-2].ts


def test_snapshot_computes_inventory_exposure_drawdown(candles):
    last = candles[-1].c
    acct = AccountView(equity=9_000, cash=4_000, peak_equity=10_000, day_start_equity=9_500,
                       positions={"BTC/USDT": Position("BTC/USDT", 4_500 / last, last, last * .9, last * 1.1, 0)})
    snap = build_snapshot("BTC/USDT", "1h", candles, candles[-1].ts + H, acct)
    assert snap["inventory_pct"] == pytest.approx(50.0, abs=0.01)
    assert snap["acct_dd_pct"] == pytest.approx(10.0, abs=0.01)
    assert snap["day_loss_pct"] == pytest.approx(5.26, abs=0.01)
    assert snap["imbalance"] is None    # no book -> toxic_flow must be null downstream


def _healthy(candles):
    now = candles[-1].ts + H + 5_000
    return now, make_book(candles[-1].c, ts=now - 1000)


def test_health_ok(candles):
    now, book = _healthy(candles)
    assert health_issues(candles, H, now, book, now + 100) == []


def test_health_catches_missing_stale_error_skew(candles):
    now, book = _healthy(candles)
    gap = candles[:250] + candles[251:]
    assert any("candle_gaps" in i for i in health_issues(gap, H, now, book, now))
    assert any(i.startswith("stale_feed:candles") for i in health_issues(candles, H, now + 3 * H, book, now + 3 * H))
    stale_book = make_book(candles[-1].c, ts=now - 60_000)
    assert any(i.startswith("stale_feed:book") for i in health_issues(candles, H, now, stale_book, now))
    assert any(i.startswith("clock_skew") for i in health_issues(candles, H, now, book, now + 5_000))
    assert any(i.startswith("clock_skew") for i in health_issues(candles, H, now, book, None))
    assert any(i.startswith("exchange_error") for i in health_issues(candles, H, now, book, now, exchange_error="Timeout"))
    assert any("no_order_book" in i for i in health_issues(candles, H, now, None, now))
    assert any("only_10" in i for i in health_issues(candles[:10], H, now, book, now))
    crossed = make_book(candles[-1].c, spread_bps=-5, ts=now)
    assert any("crossed_book" in i for i in health_issues(candles, H, now, crossed, now))
    wide = make_book(candles[-1].c, spread_bps=300, ts=now)
    assert any("spread_too_wide" in i for i in health_issues(candles, H, now, wide, now))
    bad = candles[:-1] + [dataclasses.replace(candles[-1], c=float("nan"))]
    assert any("bad_candle_values" in i for i in health_issues(bad, H, now, book, now))


# ---------------- risk layer ----------------

def _buy(qty=1.0, px=100.0, stop=95.0, sym="BTC/USDT"):
    return OrderIntent(sym, "buy", qty, px, False, stop, px * 1.1, "t")


def test_default_limits_match_spec():
    L = RiskLimits()
    assert (L.risk_per_trade, L.max_daily_loss, L.max_open_positions, L.max_leverage, L.kill_drawdown) == \
           (0.02, 0.05, 3, 2.0, 0.15)


def test_absurd_limits_rejected():
    for kw in ({"risk_per_trade": 0.5}, {"max_daily_loss": 0.9}, {"kill_drawdown": 0.95}, {"max_open_positions": 0}):
        with pytest.raises(ValueError):
            RiskLimits(**kw)


def test_risk_per_trade_clamps_size(account):
    v = RiskEngine(RiskLimits()).check(_buy(qty=1000, stop=95), account, {"BTC/USDT": 100.0})
    # 2% of 10,000 = 200 at risk; 5 per unit -> 40 units
    assert v.approved and v.qty == pytest.approx(40.0) and "clamped:risk_per_trade" in v.reasons


def test_spot_leverage_and_cash_cap(account):
    # tight stop would allow a huge notional; spot caps exposure at 1x equity and at free cash
    v = RiskEngine(RiskLimits()).check(_buy(qty=10_000, stop=99.9), account, {"BTC/USDT": 100.0})
    assert v.approved and v.qty * 100 <= account.cash


def test_two_x_cap_applies_when_venue_allows():
    L = RiskLimits(venue_max_leverage=5.0)
    assert L.effective_leverage == 2.0


def test_order_without_stop_rejected(account):
    v = RiskEngine(RiskLimits()).check(_buy(stop=0.0), account, {"BTC/USDT": 100.0})
    assert not v.approved and "missing_or_invalid_stop" in v.reasons


def test_daily_loss_blocks_new_risk():
    acct = AccountView(equity=9_400, cash=9_400, peak_equity=10_000, day_start_equity=10_000)
    v = RiskEngine(RiskLimits()).check(_buy(), acct, {"BTC/USDT": 100.0})
    assert not v.approved and any("daily_loss" in r for r in v.reasons)


def test_max_open_positions():
    pos = {s: Position(s, 1, 100, 90, 120, 0) for s in ("A/USDT", "B/USDT", "C/USDT")}
    acct = AccountView(10_000, 9_700, 10_000, 10_000, pos)
    v = RiskEngine(RiskLimits()).check(_buy(sym="D/USDT"), acct, {})
    assert not v.approved and any("max_open_positions" in r for r in v.reasons)


def test_kill_switch_latches_persists_and_needs_human(tmp_path):
    path = str(tmp_path / "risk.json")
    eng = RiskEngine(RiskLimits(), path)
    bad = AccountView(equity=8_400, cash=8_400, peak_equity=10_000, day_start_equity=8_400)
    assert eng.update(bad) is True
    recovered = flat_account()
    v = eng.check(_buy(), recovered, {"BTC/USDT": 100.0})
    assert not v.approved and any("kill_switch" in r for r in v.reasons)      # still latched after recovery
    assert RiskEngine(RiskLimits(), path).killed                               # survives restart
    with pytest.raises(PermissionError):
        eng.reset_kill_switch("please")
    eng.reset_kill_switch("I-RESET-THE-KILL-SWITCH")
    assert eng.check(_buy(), recovered, {"BTC/USDT": 100.0}).approved


def test_exits_allowed_even_when_killed():
    eng = RiskEngine(RiskLimits())
    eng.killed = True
    acct = AccountView(8_000, 7_000, 10_000, 8_000, {"BTC/USDT": Position("BTC/USDT", 2, 100, 90, 120, 0)})
    v = eng.check(OrderIntent("BTC/USDT", "sell", 5, 100, True), acct, {"BTC/USDT": 100.0}, health=["stale_feed"])
    assert v.approved and v.qty == 2          # never sells more than held


def test_spot_cannot_short(account):
    v = RiskEngine(RiskLimits()).check(OrderIntent("BTC/USDT", "sell", 1, 100), account, {})
    assert not v.approved


def test_off_limits_universe_and_unhealthy_data(account):
    eng = RiskEngine(RiskLimits(), off_limits={"doge"}, allowed={"BTC/USDT", "DOGE/USDT"})
    assert any("off_limits" in r for r in eng.check(_buy(sym="DOGE/USDT"), account, {}).reasons)
    assert any("not_in_universe" in r for r in eng.check(_buy(sym="PEPE/USDT"), account, {}).reasons)
    v = eng.check(_buy(), account, {"BTC/USDT": 100.0}, health=["stale_feed:book_60s_old"])
    assert not v.approved and any("unhealthy_data" in r for r in v.reasons)


def test_risk_state_levels():
    eng = RiskEngine(RiskLimits())
    assert eng.risk_state(flat_account()) == "safe"
    assert eng.risk_state(AccountView(9_200, 9_200, 10_000, 9_200)) == "near_limit"     # dd 8% of 15%
    assert eng.risk_state(AccountView(8_700, 8_700, 10_000, 8_700)) == "reduce"         # dd 13%
    assert eng.risk_state(AccountView(9_590, 9_590, 10_000, 10_000)) == "reduce"        # day loss 4.1% of 5%


# ---------------- gate and sizing ----------------

def _d(**kw):
    base = dict(regime="trending", direction="long", direction_confidence=0.9, reflex_confidence=0.85,
                toxic_flow=0.2, setup_quality=2, risk_state="safe")
    base.update(kw)
    return ReflexDecision(**base)


def test_gate_passes_only_when_every_threshold_met():
    P = Policy()
    assert gate(_d(), 0.9, "safe", P, False).action == "enter_long"
    assert gate(_d(), 0.80, "safe", P, False).action == "hold"                 # must be strictly > 0.80
    assert gate(_d(setup_quality=1), 0.9, "safe", P, False).action == "hold"
    assert gate(_d(risk_state="near_limit"), 0.9, "safe", P, False).action == "hold"
    assert gate(_d(), 0.9, "near_limit", P, False).action == "hold"            # code's view wins when stricter
    assert gate(_d(toxic_flow=0.9), 0.9, "safe", P, False).action == "hold"
    assert gate(_d(toxic_flow=None), 0.9, "safe", P, False).action == "enter_long"
    assert gate(_d(), 0.9, "safe", P, False, thesis_valid=False).action == "hold"


def test_gate_never_opens_a_short_on_spot():
    g = gate(_d(direction="short"), 0.95, "safe", Policy(), False)
    assert g.action == "hold"


def test_low_confidence_or_crisis_pauses_and_escalates():
    g = gate(_d(reflex_confidence=0.5), 0.9, "safe", Policy(), False)
    assert g.action == "hold" and g.pause_new_risk and g.escalate
    g = gate(_d(regime="crisis"), 0.9, "safe", Policy(), False)
    assert g.action == "hold" and g.escalate
    assert gate(_d(regime="crisis"), 0.9, "safe", Policy(), True).action == "exit"


def test_gate_exits():
    P = Policy()
    assert gate(_d(direction="short", direction_confidence=0.7), 0.7, "safe", P, True).action == "exit"
    assert gate(_d(), 0.9, "reduce", P, True).action == "exit"
    assert gate(_d(), 0.9, "safe", P, True, thesis_valid=False).action == "exit"
    assert gate(_d(), 0.9, "safe", P, True).action == "hold"


def test_kelly_math():
    assert kelly_fraction(0.6, 1.0) == pytest.approx(0.2)
    assert kelly_fraction(0.4, 1.0) == 0.0
    assert kelly_fraction(0.5, 0.0) == 0.0


def test_size_is_at_most_quarter_kelly_and_risk_cap():
    P = Policy()   # stop 2 ATR, target 3 ATR -> payoff 1.5
    qty, stop, target = position_size(10_000, 100.0, 0.85, 2.0, P, 0.02)
    assert stop == 96.0 and target == 106.0
    full = kelly_fraction(0.85, 1.5)                       # 0.75
    risk = qty * (100 - stop)
    assert risk == pytest.approx(min(0.25 * full, 0.02) * 10_000)
    assert risk <= 0.02 * 10_000 + 1e-9
    # weak edge: quarter Kelly binds below the 2% cap
    qty2, *_ = position_size(10_000, 100.0, 0.42, 2.0, P, 0.02)
    assert qty2 * 4 == pytest.approx(0.25 * kelly_fraction(0.42, 1.5) * 10_000)
    assert position_size(10_000, 100.0, 0.30, 2.0, P, 0.02)[0] == 0.0   # negative edge -> no trade


def test_policy_cannot_exceed_quarter_kelly():
    assert MAX_KELLY_FRACTION == 0.25
    with pytest.raises(ValueError):
        Policy(kelly_fraction=0.5)
