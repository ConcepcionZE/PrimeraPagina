"""Configuration loading. Secrets never live here: they come from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from .exchange.paper import CostModel
from .risk import RiskLimits
from .state_engine import HealthConfig


@dataclass
class Settings:
    exchange: str = "kraken"
    market: str = "spot"
    quote: str = "USDT"
    timeframe: str = "1h"
    paper_balance: float = 10_000.0
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    off_limits: list[str] = field(default_factory=list)
    reflex_provider: str = "jev"          # jev | haiku | rules
    reflex_model: str = "jev-latest"
    horizon_bars: int = 12
    data_dir: str = "data"
    policy_dir: str = "config/policies"
    schema_dir: str = "config/schemas"
    limits: RiskLimits = field(default_factory=RiskLimits)
    cost: CostModel = field(default_factory=CostModel)
    health: HealthConfig = field(default_factory=HealthConfig)


def load_settings(path: str = "config/default.yaml", risk_path: str = "config/risk.yaml") -> Settings:
    s = Settings()
    if os.path.exists(path):
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        for k in ("exchange", "market", "quote", "timeframe", "paper_balance", "symbols", "off_limits",
                  "reflex_provider", "reflex_model", "horizon_bars", "data_dir", "policy_dir", "schema_dir"):
            if k in raw:
                setattr(s, k, raw[k])
        if "cost" in raw:
            s.cost = CostModel(**raw["cost"])
        if "health" in raw:
            s.health = HealthConfig(**raw["health"])
    if os.path.exists(risk_path):
        with open(risk_path) as f:
            s.limits = RiskLimits(**(yaml.safe_load(f) or {}))
    if s.market != "spot":
        raise ValueError("this build supports spot only")
    s.symbols = [x for x in s.symbols if x.split("/")[0].upper() not in {o.upper() for o in s.off_limits}]
    s.data_dir = os.environ.get("REFLEXBOT_DATA_DIR", s.data_dir)
    s.reflex_provider = os.environ.get("REFLEX_PROVIDER", s.reflex_provider)
    return s


def make_reflex(provider: str, model: str | None = None):
    if provider == "jev":
        from .reflex.jev import JevReflex
        return JevReflex(model or "jev-latest")
    if provider == "haiku":
        from .reflex.haiku import HaikuReflex
        return HaikuReflex(model or "claude-haiku-5-5")
    if provider == "rules":
        from .reflex.rules import RuleReflex
        return RuleReflex()
    raise ValueError(f"unknown reflex provider: {provider}")
