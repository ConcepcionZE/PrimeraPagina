"""REFLEX interface. Jev, Claude Haiku 5.5 and the code-rule engine all sit behind it.

`timed_decide` measures latency on every call. A decision that errors or arrives
after the deadline is a miss: the caller skips that candle, it never trades late.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Protocol

from ..types import ReflexDecision

# Earliest date each decision model may be backtested from. A model remembers what
# happened before its training cutoff, so earlier data only tests code rules.
# Jev's cutoff is not published; its release date is used as a safe upper bound.
MODEL_CUTOFFS = {
    "jev": "2026-10-22",
    "haiku": "2026-06-30",   # Claude Haiku 5.5, cutoff June 2026
    "rules": None,           # pure code: no memory, any data is fair
}


class ReflexError(RuntimeError):
    pass


class Reflex(Protocol):
    name: str

    def decide(self, snapshot: dict) -> ReflexDecision: ...


@dataclass
class TimedResult:
    decision: Optional[ReflexDecision]
    latency_ms: float
    missed: bool
    error: str = ""


def timed_decide(reflex: Reflex, snapshot: dict, deadline_ms: float) -> TimedResult:
    t0 = time.perf_counter()
    try:
        d = reflex.decide(snapshot)
    except Exception as e:  # any failure is a miss, never a trade
        ms = (time.perf_counter() - t0) * 1000
        return TimedResult(None, ms, True, f"{type(e).__name__}: {e}"[:300])
    ms = (time.perf_counter() - t0) * 1000
    d.latency_ms = ms
    if ms > deadline_ms:
        return TimedResult(None, ms, True, f"deadline: {ms:.0f}ms > {deadline_ms:.0f}ms")
    return TimedResult(d, ms, False)


def concentration(probs: dict[str, float]) -> float:
    """Jev's documented Choice confidence: (n * p_max - 1) / (n - 1)."""
    n = len(probs)
    if n < 2:
        return 1.0
    return max(0.0, min(1.0, (n * max(probs.values()) - 1) / (n - 1)))
