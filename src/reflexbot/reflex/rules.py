"""Code-rule REFLEX: deterministic, no model, no memory of history.

Used (a) to backtest code rules on data older than any model's training cutoff,
(b) as the mock when a model API is unreachable, and (c) as the baseline a model
must beat. Its probabilities are heuristics, not calibrated estimates; the
calibrator and the Brier score say how far off they are.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..types import ReflexDecision
from .base import concentration


@dataclass(frozen=True)
class RuleParams:
    gap_min_pct: float = 0.4        # ema gap needed to call a trend
    eff_min: float = 0.30           # efficiency ratio needed to call a trend
    crisis_ret24_pct: float = -12.0
    high_vol_ann_pct: float = 110.0
    p_base: float = 0.55
    p_slope: float = 0.12           # confidence added per unit of trend strength
    rsi_low: float = 28.0
    rsi_high: float = 75.0


class RuleReflex:
    name = "rules"

    def __init__(self, params: RuleParams = RuleParams()):
        self.p = params

    def decide(self, s: dict) -> ReflexDecision:
        P = self.p
        gap, eff, rsi = s["ema_gap_pct"], s["trend_eff"], s["rsi14"]
        ret6, ret24, vol = s["ret6_pct"], s["ret24_pct"], s["vol_ann_pct"]
        atr_pct = max(s["atr_pct"], 1e-6)

        if ret24 <= P.crisis_ret24_pct and vol >= P.high_vol_ann_pct:
            regime = "crisis"
        elif abs(gap) >= P.gap_min_pct and eff >= P.eff_min:
            regime = "trending"
        elif vol >= P.high_vol_ann_pct:
            regime = "high_vol"
        else:
            regime = "mean_reverting"

        p_long, p_short = 0.25, 0.25
        if regime == "trending":
            strength = min(3.0, abs(gap) / P.gap_min_pct * eff / P.eff_min / 2)
            p = min(0.95, P.p_base + P.p_slope * strength)
            if gap > 0 and ret6 > -atr_pct:
                p_long, p_short = p, (1 - p) * 0.4
            elif gap < 0:
                p_short, p_long = p, (1 - p) * 0.4
        elif regime == "mean_reverting":
            if rsi <= P.rsi_low:
                p_long, p_short = 0.62, 0.15
            elif rsi >= P.rsi_high:
                p_short, p_long = 0.62, 0.15
        elif regime == "crisis":
            p_short, p_long = 0.7, 0.1
        probs = {"long": p_long, "short": p_short, "neutral": max(0.0, 1 - p_long - p_short)}
        direction = max(probs, key=lambda k: probs[k])

        q = 0
        if regime == "trending" and gap > 0:
            q += 1
            if 45 <= rsi <= 70 and s["range_pos"] >= 0.5:
                q += 1                      # trend intact, not exhausted
            if s["volume_z"] > 0 and (s.get("spread_bps") is None or s["spread_bps"] < 15):
                q += 1
        elif regime == "mean_reverting" and rsi <= P.rsi_low:
            q = 1

        imb = s.get("imbalance")
        toxic = None if imb is None else max(0.0, min(1.0, 0.5 - imb * 0.8 + max(0.0, s["volume_z"] - 2) * 0.1))

        dd, dl = s["acct_dd_pct"], s["day_loss_pct"]
        risk_state = "reduce" if (dd >= 12 or dl >= 4) else "near_limit" if (dd >= 7.5 or dl >= 2.5) else "safe"

        return ReflexDecision(
            regime=regime, direction=direction,
            direction_confidence=probs[direction],
            reflex_confidence=concentration(probs),
            toxic_flow=toxic, setup_quality=min(3, q), risk_state=risk_state, model="rules-v1",
        )
