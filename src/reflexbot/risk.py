"""Hard risk layer. No model can override it and no model is called from it.

Every order passes through RiskEngine.check() before it reaches an exchange
adapter. The limits are plain numbers loaded from config/risk.yaml, which only a
human edits; the nightly loop has no write path to them.

Defaults (until the owner changes them):
  2% of the account at risk per trade, 5% max daily loss, 3 open positions,
  2x leverage, kill switch at 15% drawdown.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

from .types import AccountView, OrderIntent, RiskState


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.02
    max_daily_loss: float = 0.05
    max_open_positions: int = 3
    max_leverage: float = 2.0
    kill_drawdown: float = 0.15
    # Venue cap: spot cannot borrow, so exposure never exceeds equity there.
    venue_max_leverage: float = 1.0
    near_limit_fraction: float = 0.5
    reduce_fraction: float = 0.8

    def __post_init__(self):
        if not (0 < self.risk_per_trade <= 0.05):
            raise ValueError("risk_per_trade must be in (0, 0.05]")
        if not (0 < self.max_daily_loss <= 0.25):
            raise ValueError("max_daily_loss must be in (0, 0.25]")
        if not (0 < self.kill_drawdown <= 0.5):
            raise ValueError("kill_drawdown must be in (0, 0.5]")
        if self.max_open_positions < 1 or self.max_leverage <= 0:
            raise ValueError("max_open_positions >= 1 and max_leverage > 0 required")

    @property
    def effective_leverage(self) -> float:
        return min(self.max_leverage, self.venue_max_leverage)


@dataclass
class RiskVerdict:
    approved: bool
    qty: float
    reasons: list[str]


class RiskEngine:
    """Stateful only for the kill switch, which latches until a human resets it."""

    def __init__(self, limits: RiskLimits, state_path: Optional[str] = None,
                 off_limits: Optional[set[str]] = None, allowed: Optional[set[str]] = None):
        self.limits = limits
        self.state_path = state_path
        self.off_limits = {s.upper() for s in (off_limits or set())}
        self.allowed = allowed
        self.killed = False
        self.kill_reason = ""
        if state_path and os.path.exists(state_path):
            with open(state_path) as f:
                st = json.load(f)
            self.killed = bool(st.get("killed"))
            self.kill_reason = st.get("kill_reason", "")

    # ----- kill switch -----
    def _persist(self) -> None:
        if self.state_path:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"killed": self.killed, "kill_reason": self.kill_reason,
                           "limits": asdict(self.limits)}, f)
            os.replace(tmp, self.state_path)

    def update(self, account: AccountView) -> bool:
        """Call on every cycle. Returns True if the kill switch is (now) on."""
        if not self.killed and account.drawdown >= self.limits.kill_drawdown:
            self.killed = True
            self.kill_reason = f"drawdown {account.drawdown:.2%} >= {self.limits.kill_drawdown:.0%}"
            self._persist()
        return self.killed

    def reset_kill_switch(self, human_confirmation: str) -> None:
        if human_confirmation != "I-RESET-THE-KILL-SWITCH":
            raise PermissionError("kill switch reset needs the explicit human confirmation string")
        self.killed = False
        self.kill_reason = ""
        self._persist()

    # ----- state for the snapshot / gate -----
    def risk_state(self, account: AccountView) -> RiskState:
        L = self.limits
        dd = account.drawdown / L.kill_drawdown
        dl = account.daily_loss / L.max_daily_loss
        worst = max(dd, dl)
        if self.killed or worst >= L.reduce_fraction:
            return "reduce"
        if worst >= L.near_limit_fraction or len(account.positions) >= L.max_open_positions:
            return "near_limit"
        return "safe"

    # ----- the check every order goes through -----
    def check(self, intent: OrderIntent, account: AccountView, prices: dict[str, float],
              health: Optional[list[str]] = None) -> RiskVerdict:
        L = self.limits
        reasons: list[str] = []
        base = intent.symbol.split("/")[0].upper()

        if intent.qty <= 0 or intent.ref_price <= 0:
            return RiskVerdict(False, 0.0, ["invalid_order:non_positive_qty_or_price"])

        if intent.reduce_only or intent.side == "sell":
            # Spot: a sell can only reduce a long. Never sell more than we hold.
            pos = account.positions.get(intent.symbol)
            if pos is None or pos.qty <= 0:
                return RiskVerdict(False, 0.0, ["no_position_to_reduce:spot_cannot_short"])
            return RiskVerdict(True, min(intent.qty, pos.qty), ["reduce_only"])

        # ----- from here on: an order that adds risk -----
        if health:
            reasons.append("unhealthy_data:" + ";".join(health))
        if self.update(account):
            reasons.append("kill_switch:" + self.kill_reason)
        if base in self.off_limits:
            reasons.append(f"off_limits:{base}")
        if self.allowed is not None and intent.symbol not in self.allowed:
            reasons.append(f"not_in_universe:{intent.symbol}")
        if account.daily_loss >= L.max_daily_loss:
            reasons.append(f"daily_loss {account.daily_loss:.2%} >= {L.max_daily_loss:.0%}")
        if intent.symbol not in account.positions and len(account.positions) >= L.max_open_positions:
            reasons.append(f"max_open_positions {L.max_open_positions}")
        if intent.symbol in account.positions:
            reasons.append("already_in_position")
        if not (0 < intent.stop < intent.ref_price):
            reasons.append("missing_or_invalid_stop")
        if reasons:
            return RiskVerdict(False, 0.0, reasons)

        qty = intent.qty
        notes: list[str] = []
        # 1) risk per trade: loss if the stop is hit must not exceed the limit
        per_unit_risk = intent.ref_price - intent.stop
        max_qty_risk = L.risk_per_trade * account.equity / per_unit_risk
        if qty > max_qty_risk:
            qty = max_qty_risk
            notes.append("clamped:risk_per_trade")
        # 2) leverage: total exposure after the order
        exposure = account.exposure(prices)
        room = L.effective_leverage * account.equity - exposure
        if room <= 0:
            return RiskVerdict(False, 0.0, ["leverage_limit_reached"])
        if qty * intent.ref_price > room:
            qty = room / intent.ref_price
            notes.append("clamped:leverage")
        # 3) spot cash: cannot spend more than free cash (leave room for the fee)
        max_qty_cash = account.cash * 0.995 / intent.ref_price
        if qty > max_qty_cash:
            qty = max_qty_cash
            notes.append("clamped:cash")
        if qty <= 0:
            return RiskVerdict(False, 0.0, ["no_capacity"])
        return RiskVerdict(True, qty, notes or ["ok"])
