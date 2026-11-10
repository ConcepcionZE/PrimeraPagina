"""Calibration: Brier score, reliability table, and a monotone calibrator.

A REFLEX probability is only useful for sizing if "0.8" means "right about 80%
of the time". The Brier score measures that; the calibrator (pooled-adjacent-
violators over bins, fitted on past resolved decisions only) corrects it.
With too little data the calibrator shrinks toward 0.5, which shrinks size.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def brier_score(probs: list[float], outcomes: list[int]) -> float:
    if not probs:
        raise ValueError("no resolved decisions to score")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def reliability(probs: list[float], outcomes: list[int], bins: int = 5) -> list[dict]:
    rows = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [k for k, p in enumerate(probs) if (lo <= p < hi) or (i == bins - 1 and p == 1.0)]
        if idx:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx),
                         "mean_p": sum(probs[k] for k in idx) / len(idx),
                         "hit_rate": sum(outcomes[k] for k in idx) / len(idx)})
    return rows


@dataclass
class Calibrator:
    """Piecewise-constant monotone map raw p -> calibrated p. Identity until fitted."""

    edges: list[float] = field(default_factory=list)   # upper edge of each bin
    values: list[float] = field(default_factory=list)
    n: int = 0
    min_samples: int = 200       # below this, shrink toward 0.5
    prior_strength: float = 20.0  # pseudo-observations at 0.5 added to every bin

    def fit(self, probs: list[float], outcomes: list[int], bins: int = 10) -> "Calibrator":
        self.n = len(probs)
        blocks = []  # [sum_outcomes, count, upper_edge]
        for i in range(bins):
            lo, hi = i / bins, (i + 1) / bins
            idx = [k for k, p in enumerate(probs) if (lo <= p < hi) or (i == bins - 1 and p == 1.0)]
            s = sum(outcomes[k] for k in idx) + 0.5 * self.prior_strength
            blocks.append([s, len(idx) + self.prior_strength, hi])
        # pool adjacent violators so the map is non-decreasing
        merged: list[list[float]] = []
        for b in blocks:
            merged.append(list(b))
            while len(merged) > 1 and merged[-2][0] / merged[-2][1] > merged[-1][0] / merged[-1][1]:
                s, c, hi = merged.pop()
                merged[-1][0] += s
                merged[-1][1] += c
                merged[-1][2] = hi
        self.edges = [m[2] for m in merged]
        self.values = [m[0] / m[1] for m in merged]
        return self

    def __call__(self, p: float) -> float:
        if not self.edges:
            return p
        cal = self.values[-1]
        for edge, v in zip(self.edges, self.values):
            if p < edge:
                cal = v
                break
        if self.n < self.min_samples:
            w = self.n / self.min_samples
            cal = w * cal + (1 - w) * min(p, 0.5 + (p - 0.5) * 0.5)
        return max(0.0, min(1.0, cal))

    def to_dict(self) -> dict:
        return {"edges": self.edges, "values": self.values, "n": self.n,
                "min_samples": self.min_samples, "prior_strength": self.prior_strength}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Calibrator":
        return cls(**d) if d else cls()


def direction_outcome(direction: str, entry: float, exit_: float, min_move: float = 0.0) -> int | None:
    """1 if the predicted direction was right over the horizon, 0 if wrong, None if not scorable."""
    if direction == "long":
        return int(exit_ > entry * (1 + min_move))
    if direction == "short":
        return int(exit_ < entry * (1 - min_move))
    return None
