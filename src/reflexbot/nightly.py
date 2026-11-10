"""Nightly self-improvement loop. It proposes; it never promotes.

Each night:
  1. Read every fill and miss since the last run; resolve decision outcomes.
  2. Measure calibration (Brier score, reliability table).
  3. Propose a candidate policy: refit the calibrator, and try threshold changes
     inside fixed bounds.
  4. Run the candidate through the gates: replay of the journaled REFLEX
     decisions on real candles (walk-forward: calibrator fitted on the first
     part, judged on the rest), regression tests, then a paper stage.
  5. Version the candidate, keep the active policy as rollback, and alert.

Promotion to active is a separate, human-only command (policy_store.promote).
Risk limits are out of reach: a policy cannot carry them and this module never
opens config/risk.yaml.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import replace
from typing import Optional

from .backtest import run_backtest
from .calibration import Calibrator, brier_score, reliability
from .gate import Policy
from .journal import Journal
from .policy_store import PolicyStore
from .state_engine import MIN_BARS, TF_MS
from .types import Candle, ReflexDecision

MIN_RESOLVED = 100          # below this, no threshold change is even proposed
MIN_PAPER_DAYS = 7
BOUNDS = {"min_direction_confidence": (0.60, 0.95), "min_setup_quality": (1, 3),
          "stop_atr": (1.0, 4.0), "target_atr": (1.5, 6.0), "kelly_fraction": (0.05, 0.25)}


class ReplayReflex:
    """Replays journaled decisions so new thresholds can be tested without new model calls."""

    def __init__(self, name: str, decisions: dict[tuple[str, int], dict]):
        self.name = name
        self.decisions = decisions

    def decide(self, snapshot: dict) -> ReflexDecision:
        return ReflexDecision(**self.decisions[(snapshot["sym"], snapshot["t"])])


def within_bounds(policy: Policy) -> bool:
    return all(lo <= getattr(policy, k) <= hi for k, (lo, hi) in BOUNDS.items())


def candidates(policy: Policy) -> list[Policy]:
    """Threshold variants around the current policy, never outside BOUNDS."""
    out = []
    lo_c, hi_c = BOUNDS["min_direction_confidence"]
    lo_q, hi_q = BOUNDS["min_setup_quality"]
    for dc in (-0.05, 0.0, 0.05):
        for dq in (0, 1):
            conf = round(policy.min_direction_confidence + dc, 2)
            qual = policy.min_setup_quality + dq
            if not (lo_c <= conf <= hi_c and lo_q <= qual <= hi_q):
                continue
            c = replace(policy, min_direction_confidence=conf, min_setup_quality=qual)
            if within_bounds(c) and c != policy:
                out.append(c)
    return out


def run_regression_tests(repo_root: str = ".") -> tuple[bool, str]:
    if not os.path.isdir(os.path.join(repo_root, "tests")):
        return False, "tests directory not found"
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "tests"], cwd=repo_root,
                       capture_output=True, text=True, timeout=900)
    return p.returncode == 0, (p.stdout.strip().splitlines() or [""])[-1]


def nightly_review(journal: Journal, store: PolicyStore, candles: dict[str, list[Candle]], tf: str,
                   now_ms: int, horizon: int = 12, initial: float = 10_000.0, out_dir: str = "data/nightly",
                   run_tests: bool = True, repo_root: str = ".", limits=None, cost=None) -> dict:
    tf_ms = TF_MS[tf]
    since = now_ms - 86_400_000

    def price_at(symbol: str, ts: int) -> Optional[float]:
        for c in candles.get(symbol, []):
            if c.ts + tf_ms >= ts:
                return c.c if c.ts + tf_ms <= now_ms else None
        return None

    resolved_now = journal.resolve_outcomes(price_at, horizon * tf_ms, now_ms)
    active = store.active()
    policy, calibrator = store.load()
    decisions = journal.rows("decisions")
    scored = [d for d in decisions if d["outcome"] in (0, 1)]
    fills, misses = journal.rows("fills", since), journal.rows("misses", since)
    miss_kinds: dict[str, int] = {}
    for m in misses:
        miss_kinds[m["kind"]] = miss_kinds.get(m["kind"], 0) + 1
    lat = sorted(d["latency_ms"] for d in decisions if d["ts"] >= since)

    report: dict = {
        "date": dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
        "active_policy": active,
        "last_24h": {"fills": len(fills), "misses": miss_kinds, "decisions": len(lat),
                     "latency_ms_p50": lat[len(lat) // 2] if lat else None,
                     "latency_ms_max": lat[-1] if lat else None},
        "outcomes_resolved_tonight": resolved_now,
        "scored_decisions": len(scored),
        "brier_raw": None, "brier_calibrated": None, "reliability": [],
        "proposal": None, "gates": {}, "promoted": False,
    }
    if scored:
        raw = [json.loads(d["decision"])["direction_confidence"] for d in scored]
        outs = [d["outcome"] for d in scored]
        report["brier_raw"] = round(brier_score(raw, outs), 4)
        report["brier_calibrated"] = round(brier_score([calibrator(p) for p in raw], outs), 4)
        report["reliability"] = reliability(raw, outs)

    # ---- advance candidates already in the paper stage ----
    for v in store.versions():
        doc = store.load_doc(v)
        if doc["status"] == "paper":
            age_days = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(doc["created"])).days
            if age_days >= MIN_PAPER_DAYS:
                store.set_status(v, "approved_pending_owner", {"paper_days": age_days})
                report.setdefault("awaiting_owner", []).append(v)

    if len(scored) < MIN_RESOLVED:
        report["proposal"] = f"none: {len(scored)} resolved decisions, need {MIN_RESOLVED} before proposing changes"
        return _write(report, out_dir)

    # ---- propose: refit calibrator on the first 70%, judge on the last 30% ----
    scored.sort(key=lambda d: d["ts"])
    cut = int(len(scored) * 0.7)
    train, test = scored[:cut], scored[cut:]
    new_cal = Calibrator().fit([json.loads(d["decision"])["direction_confidence"] for d in train],
                               [d["outcome"] for d in train])
    test_raw = [json.loads(d["decision"])["direction_confidence"] for d in test]
    test_out = [d["outcome"] for d in test]
    brier_old = brier_score([calibrator(p) for p in test_raw], test_out)
    brier_new = brier_score([new_cal(p) for p in test_raw], test_out)

    # ---- replay journaled decisions over the test period under each candidate ----
    model = (scored[-1]["model"] or "jev").split("-")[0]
    replay_name = {"claude": "haiku"}.get(model, model if model in ("jev", "rules") else "jev")
    dec_map = {(d["symbol"], d["ts"] // 1000): json.loads(d["decision"]) for d in decisions}
    symbols = sorted({d["symbol"] for d in decisions} & set(candles))
    test_start_ts = test[0]["ts"]
    ref = candles[symbols[0]]
    idx = {c.ts + tf_ms: i for i, c in enumerate(ref)}
    covered = sorted(i for t, i in idx.items() if all((s, t // 1000) in dec_map for s in symbols))
    span = [i for i in covered if ref[i].ts + tf_ms >= test_start_ts and i >= MIN_BARS]
    replay_ok = len(span) >= 24 and span == list(range(span[0], span[-1] + 1))
    results = {}
    if replay_ok:
        data = {s: candles[s] for s in symbols}
        kw = dict(initial=initial, horizon=horizon)
        if limits:
            kw["limits"] = limits
        if cost:
            kw["cost"] = cost
        for name, pol, cal in [("current", policy, calibrator)] + [
                (f"cand{i}", c, new_cal) for i, c in enumerate([policy] + candidates(policy))]:
            r = run_backtest(data, tf, ReplayReflex(replay_name, dec_map), pol, span[0], span[-1] + 2, calibrator=cal, **kw)
            results[name] = (pol, cal, r)
    best_name, best = None, None
    for name, (pol, cal, r) in results.items():
        if name == "current":
            continue
        score = r.total_return - 0.5 * r.max_drawdown
        if best is None or score > best[3]:
            best_name, best = name, (pol, cal, r, score)

    gates = {"brier_not_worse": brier_new <= brier_old + 1e-9,
             "replay_available": replay_ok}
    if replay_ok and best:
        cur = results["current"][2]
        gates["replay_return_not_worse"] = best[2].total_return >= cur.total_return - 1e-9
        gates["replay_drawdown_not_worse"] = best[2].max_drawdown <= cur.max_drawdown + 0.01
    if run_tests:
        ok, line = run_regression_tests(repo_root)
        gates["regression_tests"] = ok
        report["regression_tests_summary"] = line
    report["gates"] = gates
    report["brier_test_old"], report["brier_test_new"] = round(brier_old, 4), round(brier_new, 4)

    version = store.next_version()
    cand_policy = replace(best[0] if best else policy, version=version)
    passed = all(gates.values())
    evidence = {"gates": gates, "brier_test_old": brier_old, "brier_test_new": brier_new,
                "n_scored": len(scored),
                "replay": {k: v[2].summary() for k, v in results.items()} if results else None}
    store.save(cand_policy, new_cal, status="paper" if passed else "gates_failed",
               notes=f"nightly proposal {report['date']} from {active}", evidence=evidence, parent=active)
    report["proposal"] = {"version": version, "status": "paper" if passed else "gates_failed",
                          "changes": {k: v for k, v in cand_policy.to_dict().items()
                                      if k != "version" and getattr(policy, k) != v},
                          "calibrator_refit": True,
                          "next": (f"paper stage for {MIN_PAPER_DAYS} days, then waits for the owner: "
                                   f"`reflexbot promote {version} --by <name>`") if passed else "rejected by gates; active policy unchanged"}
    return _write(report, out_dir)


def _write(report: dict, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{report['date']}.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    return report
