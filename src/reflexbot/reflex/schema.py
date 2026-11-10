"""The typed question set the REFLEX answers on every candle.

One schema per finalist is compiled to config/schemas/<SYMBOL>.json: the same
five typed questions plus that finalist's thesis block, which the state engine
places in the snapshot. Allowed values are fixed here; a model cannot return
anything outside them.
"""
from __future__ import annotations

import json
import os

SCHEMA_VERSION = "1"

QUESTIONS: dict = {
    "regime": {
        "type": "choice",
        "instructions": "Which market regime does this snapshot show for this asset?",
        "criteria": {
            "trending": "Persistent directional move; trend_eff high, ema_gap_pct and returns agree",
            "mean_reverting": "Range-bound; moves fade, trend_eff low, volatility ordinary",
            "high_vol": "Large erratic moves without a clean direction",
            "crisis": "Disorderly sell-off: very large losses, extreme volatility, liquidity thinning",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": "Over the next 12 candles, which direction is this asset's price more likely to take?",
        "criteria": {
            "long": "Price more likely to be higher",
            "short": "Price more likely to be lower",
            "neutral": "No usable edge either way",
        },
    },
    "setup_quality": {
        "type": "score",
        "instructions": "How good is the long entry setup right now, given trend, pullback depth, volume and spread?",
        "criteria": [
            "No setup: nothing favors an entry",
            "Weak setup: one supporting signal, others absent or against",
            "Good setup: several signals agree and risk is defined",
            "Excellent setup: trend, timing, volume and liquidity all agree",
        ],
    },
    "risk_state": {
        "type": "choice",
        "instructions": "Given acct_dd_pct, day_loss_pct, exposure_pct and open_pos, what is the account's risk state?",
        "criteria": {
            "safe": "Losses and exposure are well inside limits",
            "near_limit": "Losses or exposure are approaching limits; add no size",
            "reduce": "Losses or exposure are at limits; cut risk",
        },
    },
}

# Asked only when the snapshot carries order-book data; otherwise toxic_flow is null.
TOXIC_FLOW_QUESTION: dict = {
    "type": "noul",
    "instructions": "Order flow is toxic: a new buy order here faces high adverse-selection risk "
                    "(imbalance against it, widening spread, abnormal volume).",
}

# JSON Schema of the decision, used for Claude Haiku 5.5 structured outputs and validation.
DECISION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": ["trending", "mean_reverting", "high_vol", "crisis"]},
        "direction": {"type": "string", "enum": ["long", "short", "neutral"]},
        "p_long": {"type": "number"},
        "p_short": {"type": "number"},
        "p_neutral": {"type": "number"},
        "toxic_flow": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "setup_quality": {"type": "integer", "enum": [0, 1, 2, 3]},
        "risk_state": {"type": "string", "enum": ["safe", "near_limit", "reduce"]},
    },
    "required": ["regime", "direction", "p_long", "p_short", "p_neutral",
                 "toxic_flow", "setup_quality", "risk_state"],
    "additionalProperties": False,
}


def questions_for(snapshot: dict) -> dict:
    q = dict(QUESTIONS)
    if snapshot.get("imbalance") is not None:
        q["toxic_flow"] = TOXIC_FLOW_QUESTION
    return q


def compile_schema(finalist: dict) -> dict:
    """Compile one research finalist into the schema file the runtime loads."""
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": finalist["symbol"],
        "thesis": {
            "bias": finalist.get("bias", "long"),
            "invalid_below": finalist.get("invalid_below"),
            "summary": finalist.get("summary", ""),
            "invalidation": finalist.get("invalidation", ""),
        },
        "questions": {**QUESTIONS, "toxic_flow": TOXIC_FLOW_QUESTION},
        "decision_type": {
            "regime": ["trending", "mean_reverting", "high_vol", "crisis"],
            "direction": ["long", "short", "neutral"],
            "toxic_flow": "number in [0,1] or null",
            "setup_quality": [0, 1, 2, 3],
            "risk_state": ["safe", "near_limit", "reduce"],
        },
    }


def write_schemas(finalists: list[dict], out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for f in finalists:
        path = os.path.join(out_dir, f["symbol"].replace("/", "-") + ".json")
        with open(path, "w") as fh:
            json.dump(compile_schema(f), fh, indent=2)
            fh.write("\n")
        paths.append(path)
    return paths


def load_thesis(schema_dir: str, symbol: str) -> dict | None:
    path = os.path.join(schema_dir, symbol.replace("/", "-") + ".json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh).get("thesis")
