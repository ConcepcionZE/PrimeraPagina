"""Trade gate and sizing: every threshold is a number in code/policy, never in the model.

The REFLEX only judges (regime, direction, confidence, toxic flow, setup quality,
risk state). This module decides what that judgement is allowed to cause.

The starting thresholds (setup_quality >= 2, direction_confidence > 0.80,
risk_state == safe, quarter Kelly) are hypotheses to be tested, not evidence of
an edge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Optional

from .types import ReflexDecision, RiskState, worst_risk_state

Action = Literal["enter_long", "exit", "hold"]

MAX_KELLY_FRACTION = 0.25  # hard ceiling: never more than quarter Kelly


@dataclass(frozen=True)
class Policy:
    """Versioned, tunable by the nightly loop inside the bounds below. Not risk limits."""

    version: str = "v1"
    min_setup_quality: int = 2
    min_direction_confidence: float = 0.80
    min_reflex_confidence: float = 0.60     # below this: pause new risk and escalate
    max_toxic_flow: float = 0.70
    exit_confidence: float = 0.60           # p(short) above which an open long is closed
    kelly_fraction: float = 0.25
    stop_atr: float = 2.0
    target_atr: float = 3.0
    max_hold_bars: int = 72
    deadline_ms: float = 2000.0             # a decision slower than this misses its candle

    def __post_init__(self):
        if not (0 < self.kelly_fraction <= MAX_KELLY_FRACTION):
            raise ValueError("kelly_fraction must be in (0, 0.25]")
        if not (0.5 <= self.min_direction_confidence < 1):
            raise ValueError("min_direction_confidence must be in [0.5, 1)")
        if not (0 <= self.min_setup_quality <= 3):
            raise ValueError("min_setup_quality must be 0..3")
        if self.stop_atr <= 0 or self.target_atr <= 0:
            raise ValueError("stop_atr and target_atr must be positive")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateResult:
    action: Action
    reasons: list[str]
    pause_new_risk: bool = False
    escalate: bool = False
    risk_state: RiskState = "safe"


def gate(
    d: ReflexDecision,
    p_calibrated: float,
    code_risk_state: RiskState,
    policy: Policy,
    in_position: bool,
    thesis_valid: Optional[bool] = True,
) -> GateResult:
    """Turn a REFLEX judgement into an allowed action. Spot only: no short entries."""
    rs = worst_risk_state(d.risk_state, code_risk_state)
    pause = d.reflex_confidence < policy.min_reflex_confidence or d.regime == "crisis"
    res = GateResult("hold", [], pause_new_risk=pause, escalate=pause, risk_state=rs)

    if in_position:
        if d.regime == "crisis":
            return GateResult("exit", ["regime_crisis"], True, True, rs)
        if rs == "reduce":
            return GateResult("exit", ["risk_state_reduce"], pause, pause, rs)
        if thesis_valid is False:
            return GateResult("exit", ["thesis_invalidated"], pause, pause, rs)
        if d.direction == "short" and p_calibrated > policy.exit_confidence:
            return GateResult("exit", ["reflex_direction_short"], pause, pause, rs)
        res.reasons.append("holding")
        return res

    blockers: list[str] = []
    if pause:
        blockers.append("paused:low_confidence_or_crisis")
    if d.direction != "long":
        blockers.append(f"direction_{d.direction}")
    if d.setup_quality < policy.min_setup_quality:
        blockers.append(f"setup_quality_{d.setup_quality}<{policy.min_setup_quality}")
    if not p_calibrated > policy.min_direction_confidence:
        blockers.append(f"confidence_{p_calibrated:.2f}<={policy.min_direction_confidence:.2f}")
    if rs != "safe":
        blockers.append(f"risk_state_{rs}")
    if d.toxic_flow is not None and d.toxic_flow > policy.max_toxic_flow:
        blockers.append(f"toxic_flow_{d.toxic_flow:.2f}")
    if thesis_valid is False:
        blockers.append("thesis_invalidated")
    if blockers:
        res.reasons = blockers
        return res
    return GateResult("enter_long", ["all_gates_passed"], False, False, rs)


def kelly_fraction(p: float, payoff: float) -> float:
    """Full-Kelly fraction of bankroll to risk for win probability p and win/loss payoff ratio."""
    if payoff <= 0 or not (0 < p < 1):
        return 0.0
    return max(0.0, p - (1 - p) / payoff)


def position_size(
    equity: float,
    price: float,
    p_calibrated: float,
    atr_abs: float,
    policy: Policy,
    risk_per_trade_cap: float,
) -> tuple[float, float, float]:
    """Return (qty, stop, target). Risk budget = min(fractional Kelly, hard per-trade cap).

    The amount at risk is what is lost if the stop is hit; qty follows from the
    stop distance. The risk layer re-checks the cap independently.
    """
    stop = price - policy.stop_atr * atr_abs
    target = price + policy.target_atr * atr_abs
    if stop <= 0 or atr_abs <= 0:
        return 0.0, 0.0, 0.0
    payoff = (target - price) / (price - stop)
    f = min(policy.kelly_fraction, MAX_KELLY_FRACTION) * kelly_fraction(p_calibrated, payoff)
    risk_budget = min(f, risk_per_trade_cap) * equity
    qty = risk_budget / (price - stop)
    return max(0.0, qty), stop, target
