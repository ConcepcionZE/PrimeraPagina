"""Typed contracts shared by every layer.

The REFLEX returns a ReflexDecision and nothing else. Code turns it into an
order (or not). No field here is a threshold: thresholds live in the policy and
hard limits live in risk.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

Regime = Literal["trending", "mean_reverting", "high_vol", "crisis"]
Direction = Literal["long", "short", "neutral"]
RiskState = Literal["safe", "near_limit", "reduce"]

RISK_ORDER = {"safe": 0, "near_limit": 1, "reduce": 2}


def worst_risk_state(a: RiskState, b: RiskState) -> RiskState:
    """The more conservative of two risk states."""
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b


class ReflexDecision(BaseModel):
    """What the REFLEX judges on one candle. Typed, bounded, no free text."""

    regime: Regime
    direction: Direction
    # Probability the model assigns to the chosen direction (0..1), before calibration.
    direction_confidence: float = Field(ge=0.0, le=1.0)
    # How concentrated the direction distribution is (0 = even split, 1 = certain).
    reflex_confidence: float = Field(ge=0.0, le=1.0)
    # Probability that order flow is toxic; None when there is no order book to judge.
    toxic_flow: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    setup_quality: int = Field(ge=0, le=3)
    risk_state: RiskState
    model: str = "unknown"
    latency_ms: float = 0.0


@dataclass(frozen=True)
class Candle:
    ts: int  # open time, ms since epoch (UTC)
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class OrderBook:
    bids: list[tuple[float, float]]  # (price, qty), best first
    asks: list[tuple[float, float]]
    ts: int  # ms when the book was observed


@dataclass
class Position:
    symbol: str
    qty: float
    entry: float
    stop: float
    target: float
    opened_ts: int
    fees_paid: float = 0.0


@dataclass
class AccountView:
    """What the risk layer needs to know about the account, in quote currency."""

    equity: float
    cash: float
    peak_equity: float
    day_start_equity: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def daily_loss(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.day_start_equity)

    def exposure(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(s, p.entry) for s, p in self.positions.items())


@dataclass
class OrderIntent:
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    ref_price: float
    reduce_only: bool = False
    stop: float = 0.0
    target: float = 0.0
    reason: str = ""


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    ts: int
    slippage_bps: float = 0.0
    order_id: str = ""
