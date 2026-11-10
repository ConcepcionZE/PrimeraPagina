"""Jev (TypeSafe System One) adapter.

API as documented at https://docs.typesafe.ai/api/system-one (read 2026-11-10):
  POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <key>
  body: {"model", "state", "questions": {name: {"type": choice|score|noul, ...}}}
  answers: choice -> {choice, confidence, probabilities}; score -> {score,
  confidence, probabilities}; noul -> {noul}.

The key is read from JEV_API_KEY. It is never logged or written anywhere.
"""
from __future__ import annotations

import os

import requests

from ..types import ReflexDecision
from .base import ReflexError
from .schema import questions_for

JEV_URL = "https://api.typesafe.ai/v1/systemone"


class JevReflex:
    name = "jev"

    def __init__(self, model: str = "jev-latest", timeout_s: float = 3.0, session=None):
        self.model = model
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        # In a managed session a network proxy may inject the real credential;
        # the placeholder keeps the header well-formed there.
        self._key = os.environ.get("JEV_API_KEY") or "proxy-injected"

    def decide(self, snapshot: dict) -> ReflexDecision:
        body = {"model": self.model, "state": snapshot, "questions": questions_for(snapshot)}
        r = self.session.post(
            JEV_URL, json=body, timeout=self.timeout_s,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            raise ReflexError(f"jev http {r.status_code}: {r.text[:200]}")
        return parse_jev(r.json(), has_book=snapshot.get("imbalance") is not None)


def parse_jev(payload: dict, has_book: bool) -> ReflexDecision:
    try:
        a = payload["answers"]
        direction = a["direction"]
        probs = direction["probabilities"]
        sq = a["setup_quality"]["probabilities"]
        toxic = a["toxic_flow"]["noul"] if (has_book and "toxic_flow" in a) else None
        return ReflexDecision(
            regime=a["regime"]["choice"],
            direction=direction["choice"],
            direction_confidence=float(probs[direction["choice"]]),
            reflex_confidence=float(direction["confidence"]),
            toxic_flow=toxic,
            setup_quality=int(max(sq, key=lambda k: sq[k])),
            risk_state=a["risk_state"]["choice"],
            model=str(payload.get("model", "jev")),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ReflexError(f"jev response did not match the schema: {e}") from e
