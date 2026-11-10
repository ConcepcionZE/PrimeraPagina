"""Claude Haiku 5.5 adapter (fallback when Jev is not available).

Low effort, structured outputs constrained to the decision JSON schema, behind
the same interface as Jev. Needs `pip install reflexbot[haiku]` and
ANTHROPIC_API_KEY. Not exercised against the live API in this build (no
Anthropic key in the environment); the parsing path is unit-tested.
"""
from __future__ import annotations

import json

from ..types import ReflexDecision
from .base import ReflexError, concentration
from .schema import DECISION_JSON_SCHEMA, QUESTIONS

SYSTEM = (
    "You are the REFLEX of a trading system. You receive one numeric market snapshot as JSON "
    "and return one typed judgement. You do not size, place or approve orders; code does. "
    "Give honest probabilities for long, short and neutral over the next 12 candles that sum to 1. "
    "Set toxic_flow to null when the snapshot has no order-book fields (imbalance is null).\n"
    "Definitions:\n" + json.dumps(QUESTIONS, separators=(",", ":"))
)


class HaikuReflex:
    name = "haiku"

    def __init__(self, model: str = "claude-haiku-5-5", timeout_s: float = 8.0, client=None):
        self.model = model
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.Anthropic(timeout=timeout_s, max_retries=0)
        self.client = client

    def decide(self, snapshot: dict) -> ReflexDecision:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(snapshot, separators=(",", ":"))}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": DECISION_JSON_SCHEMA},
            },
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ReflexError("haiku refused the request")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ReflexError("haiku returned no text block")
        return parse_haiku(json.loads(text), self.model)


def parse_haiku(data: dict, model: str = "claude-haiku-5-5") -> ReflexDecision:
    try:
        raw = {"long": float(data["p_long"]), "short": float(data["p_short"]),
               "neutral": float(data["p_neutral"])}
        total = sum(raw.values())
        if total <= 0 or min(raw.values()) < 0:
            raise ValueError("direction probabilities must be non-negative and sum to > 0")
        probs = {k: v / total for k, v in raw.items()}
        direction = max(probs, key=lambda k: probs[k])
        tf = data["toxic_flow"]
        return ReflexDecision(
            regime=data["regime"],
            direction=direction,  # argmax of the probabilities, so the two can never disagree
            direction_confidence=probs[direction],
            reflex_confidence=concentration(probs),
            toxic_flow=None if tf is None else min(1.0, max(0.0, float(tf))),
            setup_quality=int(data["setup_quality"]),
            risk_state=data["risk_state"],
            model=model,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ReflexError(f"haiku response did not match the schema: {e}") from e
