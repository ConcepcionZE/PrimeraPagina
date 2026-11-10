import json
import logging
import time

import pytest

from conftest import make_book
from reflexbot.alerts import Alerter
from reflexbot.calibration import Calibrator, brier_score, direction_outcome, reliability
from reflexbot.exchange.paper import CostModel, OrderRejected, PaperBroker, reconcile
from reflexbot.live_gate import APPROVAL_PHRASE, LiveNotApproved, live_status, require_live_approval
from reflexbot.reflex.base import ReflexError, concentration, timed_decide
from reflexbot.reflex.haiku import HaikuReflex, parse_haiku
from reflexbot.reflex.jev import JevReflex, parse_jev
from reflexbot.reflex.rules import RuleReflex
from reflexbot.reflex.schema import DECISION_JSON_SCHEMA, compile_schema, load_thesis, questions_for, write_schemas
from reflexbot.types import ReflexDecision

JEV_OK = {"model": "jev-1.14.0", "answers": {
    "regime": {"type": "choice", "choice": "trending", "confidence": 0.71,
               "probabilities": {"trending": 0.78, "mean_reverting": 0.1, "high_vol": 0.11, "crisis": 0.01}},
    "direction": {"type": "choice", "choice": "short", "confidence": 0.83,
                  "probabilities": {"long": 0.04, "short": 0.89, "neutral": 0.07}},
    "setup_quality": {"type": "score", "score": 1.01, "confidence": 0.77,
                      "probabilities": {"0": 0.11, "1": 0.78, "2": 0.1, "3": 0.01}},
    "risk_state": {"type": "choice", "choice": "safe", "confidence": 0.9,
                   "probabilities": {"safe": 0.93, "near_limit": 0.05, "reduce": 0.02}},
    "toxic_flow": {"type": "noul", "noul": 0.53}}, "usage": {"input_tokens": 609, "output_tokens": 121}}


# ---------------- paper broker ----------------

def test_buy_pays_fee_and_slippage_from_book():
    b = PaperBroker(10_000, CostModel(taker_fee=0.0026))
    book = make_book(100.0, spread_bps=10, depth_qty=5)
    f = b.market_order("BTC/USDT", "buy", 8, 1, 100.0, book=book, stop=95, target=110)
    assert f.price > 100.05                       # crossed the spread and walked a second level
    assert f.fee == pytest.approx(f.qty * f.price * 0.0026)
    assert b.cash == pytest.approx(10_000 - f.qty * f.price - f.fee)
    assert f.slippage_bps > 5


def test_round_trip_loses_costs_at_flat_price():
    b = PaperBroker(10_000)
    book = make_book(100.0, depth_qty=1000)
    b.market_order("BTC/USDT", "buy", 10, 1, 100.0, book=book, stop=95, target=110)
    b.market_order("BTC/USDT", "sell", 10, 2, 100.0, book=book)
    assert b.positions == {}
    assert b.cash < 10_000 and b.cash == pytest.approx(10_000 + b.realized_pnl)
    assert b.fees_paid == pytest.approx(2 * 10 * 100 * 0.0026, rel=0.01)


def test_liquidity_rejects_order_larger_than_book():
    b = PaperBroker(1_000_000)
    with pytest.raises(OrderRejected, match="insufficient_liquidity"):
        b.market_order("BTC/USDT", "buy", 10_000, 1, 100.0, book=make_book(100.0, depth_qty=5))


def test_bar_model_caps_participation_and_adds_impact():
    b = PaperBroker(1_000_000, CostModel(max_participation=0.02, base_slippage_bps=3, impact_coef_bps=100))
    f = b.market_order("BTC/USDT", "buy", 1000, 1, 100.0, bar_quote_volume=100_000, stop=95, target=110)
    assert f.qty == pytest.approx(20.0)           # 2% of 100k quote volume / 100
    assert f.slippage_bps == pytest.approx(3 + 100 * 0.02)
    with pytest.raises(OrderRejected):
        b.market_order("ETH/USDT", "buy", 1, 1, 100.0, bar_quote_volume=0, stop=95, target=110)


def test_failed_orders_and_insufficient_funds():
    b = PaperBroker(10_000, CostModel(fail_rate=1.0))
    with pytest.raises(OrderRejected, match="injected_failure"):
        b.market_order("BTC/USDT", "buy", 1, 1, 100.0, book=make_book())
    assert b.cash == 10_000 and not b.positions   # a failed order changes nothing
    b2 = PaperBroker(50)
    with pytest.raises(OrderRejected, match="insufficient_funds"):
        b2.market_order("BTC/USDT", "buy", 1, 1, 100.0, book=make_book())
    with pytest.raises(OrderRejected, match="spot_cannot_short"):
        b2.market_order("BTC/USDT", "sell", 1, 1, 100.0, book=make_book())


def test_state_survives_restart(tmp_path):
    p = str(tmp_path / "paper.json")
    b = PaperBroker(10_000, state_path=p)
    b.market_order("BTC/USDT", "buy", 3, 1, 100.0, book=make_book(depth_qty=100), stop=95, target=110)
    b2 = PaperBroker(10_000, state_path=p)
    assert b2.cash == b.cash and b2.positions["BTC/USDT"].qty == 3 and b2.positions["BTC/USDT"].stop == 95


def test_mark_tracks_peak_and_day():
    b = PaperBroker(10_000)
    b.market_order("BTC/USDT", "buy", 10, 1, 100.0, book=make_book(depth_qty=100), stop=90, target=130)
    a1 = b.mark({"BTC/USDT": 120.0}, "2026-11-10")
    a2 = b.mark({"BTC/USDT": 100.0}, "2026-11-10")
    assert a2.peak_equity == a1.equity and a2.drawdown > 0.01 and a2.daily_loss > 0.01
    a3 = b.mark({"BTC/USDT": 100.0}, "2026-11-11")
    assert a3.daily_loss == 0.0 and a3.drawdown > 0.01          # day resets, drawdown does not


def test_reconciliation_detects_mismatch():
    assert reconcile({"BTC/USDT": 1.0}, {"BTC/USDT": 1.0}) == []
    assert len(reconcile({"BTC/USDT": 1.0}, {"BTC/USDT": 0.9})) == 1
    assert len(reconcile({}, {"ETH/USDT": 2.0})) == 1


# ---------------- reflex adapters ----------------

def test_parse_jev_real_response_shape():
    d = parse_jev(JEV_OK, has_book=True)
    assert (d.regime, d.direction, d.setup_quality, d.risk_state) == ("trending", "short", 1, "safe")
    assert d.direction_confidence == 0.89 and d.reflex_confidence == 0.83 and d.toxic_flow == 0.53
    assert parse_jev(JEV_OK, has_book=False).toxic_flow is None


def test_jev_rejects_out_of_schema_and_http_errors():
    bad = json.loads(json.dumps(JEV_OK))
    bad["answers"]["direction"]["choice"] = "sideways"
    with pytest.raises(Exception):
        parse_jev(bad, True)
    with pytest.raises(ReflexError):
        parse_jev({"answers": {}}, True)

    class Resp:
        status_code, text = 429, "rate limited"

    class Sess:
        def post(self, *a, **k):
            return Resp()

    with pytest.raises(ReflexError, match="429"):
        JevReflex(session=Sess()).decide({"imbalance": None})


def test_jev_request_shape_and_no_toxic_question_without_book():
    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return JEV_OK

    class Sess:
        def post(self, url, json=None, timeout=None, headers=None):
            sent.update(url=url, body=json, headers=headers)
            return Resp()

    JevReflex(session=Sess()).decide({"sym": "BTC/USDT", "imbalance": None})
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert set(sent["body"]) == {"model", "state", "questions"}
    assert "toxic_flow" not in sent["body"]["questions"]
    assert "toxic_flow" in questions_for({"imbalance": 0.1})
    assert sent["headers"]["Authorization"].startswith("Bearer ")


def test_parse_haiku_and_direction_follows_probabilities():
    d = parse_haiku({"regime": "trending", "direction": "short", "p_long": 0.7, "p_short": 0.2, "p_neutral": 0.1,
                     "toxic_flow": None, "setup_quality": 3, "risk_state": "safe"})
    assert d.direction == "long" and d.direction_confidence == pytest.approx(0.7) and d.toxic_flow is None
    assert d.reflex_confidence == pytest.approx((3 * 0.7 - 1) / 2)
    with pytest.raises(ReflexError):
        parse_haiku({"regime": "trending"})
    assert DECISION_JSON_SCHEMA["additionalProperties"] is False


def test_haiku_adapter_sends_low_effort_structured_request():
    seen = {}

    class Block:
        type, text = "text", json.dumps({"regime": "mean_reverting", "direction": "neutral", "p_long": 0.3,
                                         "p_short": 0.3, "p_neutral": 0.4, "toxic_flow": 0.4,
                                         "setup_quality": 0, "risk_state": "safe"})

    class Msgs:
        def create(self, **kw):
            seen.update(kw)
            return type("R", (), {"content": [Block()], "stop_reason": "end_turn"})()

    d = HaikuReflex(client=type("C", (), {"messages": Msgs()})()).decide({"sym": "BTC/USDT"})
    assert d.direction == "neutral"
    assert seen["model"] == "claude-haiku-5-5" and seen["output_config"]["effort"] == "low"
    assert seen["output_config"]["format"]["type"] == "json_schema"


def test_timed_decide_measures_latency_and_misses_deadline():
    class Slow:
        name = "rules"

        def decide(self, s):
            time.sleep(0.05)
            return ReflexDecision(regime="trending", direction="long", direction_confidence=0.9,
                                  reflex_confidence=0.9, setup_quality=3, risk_state="safe")

    ok = timed_decide(Slow(), {}, deadline_ms=1000)
    assert not ok.missed and ok.decision.latency_ms >= 50
    late = timed_decide(Slow(), {}, deadline_ms=10)
    assert late.missed and late.decision is None and "deadline" in late.error


def test_timed_decide_turns_errors_into_misses():
    class Boom:
        name = "rules"

        def decide(self, s):
            raise ConnectionError("api down")

    r = timed_decide(Boom(), {}, 1000)
    assert r.missed and r.decision is None and "ConnectionError" in r.error


def test_rule_reflex_is_deterministic_and_typed(candles, account):
    from conftest import H
    from reflexbot.state_engine import build_snapshot

    snap = build_snapshot("BTC/USDT", "1h", candles, candles[-1].ts + H, account)
    a, b = RuleReflex().decide(snap), RuleReflex().decide(snap)
    assert a == b and a.toxic_flow is None and 0 <= a.setup_quality <= 3


def test_concentration_matches_jev_formula():
    assert concentration({"a": 0.8, "b": 0.15, "c": 0.05}) == pytest.approx((3 * 0.8 - 1) / 2)
    assert concentration({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}) == pytest.approx(0.0)


def test_schema_compiles_per_finalist(tmp_path):
    fin = {"symbol": "ETH/USDT", "bias": "long", "invalid_below": 1500.0, "summary": "s", "invalidation": "i"}
    sch = compile_schema(fin)
    assert sch["decision_type"]["setup_quality"] == [0, 1, 2, 3]
    assert sch["decision_type"]["regime"] == ["trending", "mean_reverting", "high_vol", "crisis"]
    assert set(sch["questions"]) == {"regime", "direction", "setup_quality", "risk_state", "toxic_flow"}
    write_schemas([fin], str(tmp_path))
    assert load_thesis(str(tmp_path), "ETH/USDT")["invalid_below"] == 1500.0
    assert load_thesis(str(tmp_path), "XRP/USDT") is None


# ---------------- calibration ----------------

def test_brier_score():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5, 0.5], [1, 0]) == 0.25
    assert brier_score([0.9, 0.9], [0, 0]) == pytest.approx(0.81)
    with pytest.raises(ValueError):
        brier_score([], [])


def test_calibrator_pulls_overconfidence_down_and_is_monotone():
    probs = [0.9] * 300 + [0.6] * 300
    outs = ([1] * 150 + [0] * 150) + ([1] * 150 + [0] * 150)     # both really 50%
    cal = Calibrator().fit(probs, outs)
    assert cal(0.9) == pytest.approx(0.5, abs=0.02)
    xs = [cal(i / 20) for i in range(21)]
    assert xs == sorted(xs)
    assert brier_score([cal(p) for p in probs], outs) < brier_score(probs, outs)


def test_calibrator_identity_when_unfitted_and_shrinks_on_small_samples():
    assert Calibrator()(0.83) == 0.83
    cal = Calibrator().fit([0.9] * 10, [1] * 10)
    assert cal(0.9) < 0.9                           # ten lucky wins do not justify 0.9
    assert Calibrator.from_dict(cal.to_dict())(0.9) == cal(0.9)


def test_direction_outcome_and_reliability():
    assert direction_outcome("long", 100, 101) == 1 and direction_outcome("long", 100, 99) == 0
    assert direction_outcome("short", 100, 99) == 1 and direction_outcome("neutral", 100, 99) is None
    rows = reliability([0.1, 0.9, 0.95], [0, 1, 0])
    assert rows[-1]["n"] == 2 and rows[-1]["hit_rate"] == 0.5


# ---------------- live gate and alerts ----------------

def test_live_gate_closed_by_default(monkeypatch, tmp_path):
    for v in ("TRADING_MODE", "KRAKEN_API_KEY", "KRAKEN_API_SECRET"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LIVE_APPROVAL_FILE", str(tmp_path / "LIVE_APPROVAL"))
    ok, missing = live_status("v1")
    assert not ok and len(missing) == 4
    with pytest.raises(LiveNotApproved):
        require_live_approval("v1")
    from reflexbot.exchange.kraken import KrakenLive
    with pytest.raises(LiveNotApproved):
        KrakenLive()


def test_live_gate_needs_all_three(monkeypatch, tmp_path):
    f = tmp_path / "LIVE_APPROVAL"
    monkeypatch.setenv("LIVE_APPROVAL_FILE", str(f))
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    assert not live_status("v1")[0]                 # no approval file
    f.write_text("yes go ahead")
    assert not live_status("v1")[0]                 # wrong phrase
    f.write_text(f"{APPROVAL_PHRASE} v2")
    assert not live_status("v1")[0]                 # approval is for a different policy
    f.write_text(f"{APPROVAL_PHRASE} v1")
    assert live_status("v1")[0]
    monkeypatch.delenv("KRAKEN_API_SECRET")
    assert not live_status("v1")[0]


def test_alerts_without_token_only_log(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    a = Alerter()
    assert not a.telegram_enabled and a.send("hello") is False


def test_alerts_send_rate_limit_and_never_log_token(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRETTOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class Sess:
        def post(self, url, json=None, timeout=None):
            calls.append((url, json))
            if len(calls) == 2:
                raise ConnectionError("down https://api.telegram.org/bot123:SECRETTOKEN/x")
            return type("R", (), {"status_code": 200})()

    a = Alerter(session=Sess())
    with caplog.at_level(logging.INFO):
        assert a.send("kill switch", key="k") is True
        assert a.send("kill switch", key="k") is False        # cooldown
        assert a.send("other") is False                       # network failure is swallowed
    assert calls[0][1] == {"chat_id": "42", "text": "kill switch"}
    assert "SECRETTOKEN" not in caplog.text
