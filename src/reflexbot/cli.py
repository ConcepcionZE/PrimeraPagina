"""Command line: one entry point for every operation. No loose scripts."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time

from .config import load_settings, make_reflex
from .gate import Policy
from .policy_store import PROMOTE_PHRASE, PolicyStore


def _fetch(settings, symbols, tf, limit=720):
    from .exchange.kraken import KrakenData
    from .state_engine import TF_MS, closed_candles

    kd = KrakenData()
    now = int(time.time() * 1000)
    data = {s: closed_candles(kd.candles(s, tf, limit), TF_MS[tf], now) for s in symbols}
    common = set.intersection(*[{c.ts for c in v} for v in data.values()])
    return {s: [c for c in v if c.ts in common] for s, v in data.items()}


def cmd_init_policy(a, s):
    store = PolicyStore(s.policy_dir)
    if store.active():
        print(f"active policy already exists: {store.active()}")
        return 0
    store.save(Policy(version="v1"), status="proposed", notes="starting hypotheses from the spec")
    store.promote("v1", approved_by=a.by, confirmation=PROMOTE_PHRASE, bootstrap=True)
    print("policy v1 created and activated (paper only)")
    return 0


def cmd_backtest(a, s):
    from .backtest import walk_forward
    from .reflex.rules import RuleParams, RuleReflex

    symbols = a.symbols.split(",") if a.symbols else s.symbols
    if "BTC/USDT" not in symbols:
        symbols = ["BTC/USDT"] + symbols
    data = _fetch(s, symbols, a.tf)
    n = min(len(v) for v in data.values())
    grid = {"min_direction_confidence": [0.6, 0.7, 0.8], "target_atr": [3.0, 4.0], "gap_min_pct": [0.4, 0.8]}
    if a.tf == "1d":
        grid["gap_min_pct"] = [2.0, 4.0]
    rep = walk_forward(data, a.tf, lambda rp: RuleReflex(RuleParams(**rp)), Policy(), grid,
                       train_bars=a.train, test_bars=a.test, initial=s.paper_balance,
                       limits=s.limits, cost=s.cost, horizon=s.horizon_bars)
    out = {"headline": rep.headline(), "timeframe": a.tf, "bars": n, "symbols": symbols,
           "reflex": "rules (code only; valid on any dates)",
           "period": [dt.datetime.fromtimestamp(rep.strategy.start_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                      dt.datetime.fromtimestamp(rep.strategy.end_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")],
           "strategy": rep.strategy.summary(), "btc_hold": rep.benchmark.summary(),
           "brier": None if rep.brier is None else round(rep.brier, 4), "n_scored": rep.n_scored,
           "folds": rep.folds}
    print(rep.headline())
    print(json.dumps({k: out[k] for k in ("period", "strategy", "btc_hold", "brier", "n_scored")}, indent=1))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    return 0


def cmd_model_backtest(a, s):
    """Replay a decision model on post-cutoff candles only. Model answers are cached on disk."""
    from .backtest import assert_after_cutoff, buy_and_hold, run_backtest
    from .calibration import brier_score, reliability
    from .nightly import ReplayReflex
    from .reflex.base import MODEL_CUTOFFS, timed_decide
    from .state_engine import MIN_BARS, TF_MS, build_snapshot
    from .types import AccountView

    symbols = a.symbols.split(",") if a.symbols else s.symbols
    if "BTC/USDT" not in symbols:
        symbols = ["BTC/USDT"] + symbols
    data = _fetch(s, symbols, a.tf)
    tf_ms = TF_MS[a.tf]
    ref = data[symbols[0]]
    cutoff = MODEL_CUTOFFS[a.provider]
    cutoff_ms = int(dt.datetime.fromisoformat(cutoff).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 86_400_000
    start = max(MIN_BARS, next(i for i, c in enumerate(ref) if c.ts + tf_ms >= cutoff_ms))
    end = len(ref)
    assert_after_cutoff(a.provider, ref[start].ts + tf_ms)
    cache_path = os.path.join(s.data_dir, f"model_cache_{a.provider}_{a.tf}.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    reflex = make_reflex(a.provider, s.reflex_model if a.provider == "jev" else None)
    flat = AccountView(s.paper_balance, s.paper_balance, s.paper_balance, s.paper_balance)
    lat, misses, calls = [], 0, 0
    for i in range(start, end - 1):
        for sym in symbols:
            key = f"{sym}|{(ref[i].ts + tf_ms) // 1000}"
            if key in cache:
                continue
            snap = build_snapshot(sym, a.tf, data[sym][max(0, i + 1 - 240): i + 1], ref[i].ts + tf_ms, flat)
            tr = timed_decide(reflex, snap, a.deadline_ms)
            calls += 1
            if tr.missed:
                misses += 1
                continue
            lat.append(tr.latency_ms)
            cache[key] = tr.decision.model_dump()
        if calls and i % 50 == 0:
            os.makedirs(s.data_dir, exist_ok=True)
            json.dump(cache, open(cache_path, "w"))
    os.makedirs(s.data_dir, exist_ok=True)
    json.dump(cache, open(cache_path, "w"))
    dec_map = {(k.split("|")[0], int(k.split("|")[1])): v for k, v in cache.items()}
    covered = [i for i in range(start, end - 1)
               if all((sym, (ref[i].ts + tf_ms) // 1000) in dec_map for sym in symbols)]
    if not covered or covered != list(range(covered[0], covered[-1] + 1)):
        print(f"model answers incomplete ({misses} misses in {calls} calls); cannot replay a continuous window")
        return 1
    store = PolicyStore(s.policy_dir)
    policy, cal = store.load()
    r = run_backtest(data, a.tf, ReplayReflex(a.provider, dec_map), policy, covered[0], covered[-1] + 2,
                     s.paper_balance, s.limits, s.cost, calibrator=cal, horizon=s.horizon_bars)
    b = buy_and_hold(data["BTC/USDT"], covered[0], covered[-1] + 2, s.cost, s.paper_balance)
    scored = [d for d in r.decisions if d["outcome"] is not None]
    lat.sort()
    out = {"model": a.provider, "cutoff_used": cutoff, "timeframe": a.tf,
           "period": [dt.datetime.fromtimestamp(r.start_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                      dt.datetime.fromtimestamp(r.end_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")],
           "beats_btc_hold": r.total_return > b.total_return,
           "strategy": r.summary(), "btc_hold": b.summary(),
           "decisions": len(r.decisions), "scored_directional": len(scored),
           "brier": round(brier_score([d["p"] for d in scored], [d["outcome"] for d in scored]), 4) if scored else None,
           "hit_rate": round(sum(d["outcome"] for d in scored) / len(scored), 3) if scored else None,
           "reliability": reliability([d["p"] for d in scored], [d["outcome"] for d in scored]) if scored else [],
           "direction_counts": {k: sum(1 for d in r.decisions if d["direction"] == k) for k in ("long", "short", "neutral")},
           "new_calls": calls, "misses": misses,
           "latency_ms": {"p50": round(lat[len(lat) // 2]), "p95": round(lat[int(len(lat) * 0.95)]),
                          "max": round(lat[-1])} if lat else None}
    verdict = "BEATS" if out["beats_btc_hold"] else "DOES NOT BEAT"
    print(f"{verdict} holding BTC after costs: {r.total_return*100:+.2f}% vs {b.total_return*100:+.2f}%")
    print(json.dumps(out, indent=1))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=2)
    return 0


def cmd_run(a, s):
    from .runner import Runner

    r = Runner(s)
    if a.once:
        print(json.dumps(r.cycle()))
        return 0
    r.run_forever(a.poll)
    return 0


def cmd_health(a, s):
    """Exit 0 if the runtime wrote a heartbeat recently. Used by the Docker health check."""
    path = os.path.join(s.data_dir, "heartbeat.json")
    if not os.path.exists(path):
        print("no heartbeat")
        return 1
    hb = json.load(open(path))
    age = time.time() - hb["ts"] / 1000
    print(json.dumps({"age_s": round(age), **hb}))
    return 0 if age < a.max_age else 1


def cmd_status(a, s):
    from .journal import Journal
    from .live_gate import live_status

    store = PolicyStore(s.policy_dir)
    ok, missing = live_status(store.active())
    out = {"active_policy": store.active(), "versions": store.versions(), "mode": "paper",
           "live_gate_open": ok, "live_gate_missing": missing}
    jp = os.path.join(s.data_dir, "journal.sqlite")
    if os.path.exists(jp):
        j = Journal(jp)
        eq = j.rows("equity")
        out.update({"decisions": len(j.rows("decisions")), "fills": len(j.rows("fills")),
                    "misses": len(j.rows("misses")), "equity": eq[-1]["equity"] if eq else None})
    print(json.dumps(out, indent=1))
    return 0


def cmd_nightly(a, s):
    from .alerts import Alerter
    from .journal import Journal
    from .nightly import nightly_review

    def once():
        j = Journal(os.path.join(s.data_dir, "journal.sqlite"))
        data = _fetch(s, s.symbols, s.timeframe)
        rep = nightly_review(j, PolicyStore(s.policy_dir), data, s.timeframe, int(time.time() * 1000),
                             s.horizon_bars, s.paper_balance, os.path.join(s.data_dir, "nightly"),
                             run_tests=not a.no_tests, limits=s.limits, cost=s.cost)
        Alerter().send(f"nightly {rep['date']}: fills {rep['last_24h']['fills']}, misses {rep['last_24h']['misses']}, "
                       f"brier {rep['brier_raw']}, proposal {rep['proposal']}")
        return rep

    if not a.loop:
        print(json.dumps(once(), indent=1, default=str))
        return 0
    while True:   # run once a day at 00:20 UTC
        now = dt.datetime.now(dt.timezone.utc)
        nxt = now.replace(hour=0, minute=20, second=0, microsecond=0)
        if nxt <= now:
            nxt += dt.timedelta(days=1)
        time.sleep((nxt - now).total_seconds())
        try:
            once()
        except Exception as e:
            logging.exception("nightly failed")
            Alerter().send(f"NIGHTLY FAILED: {type(e).__name__}: {e}", level="warn")


def cmd_promote(a, s):
    PolicyStore(s.policy_dir).promote(a.version, approved_by=a.by, confirmation=a.confirm)
    print(f"policy {a.version} is now active (approved by {a.by})")
    return 0


def cmd_rollback(a, s):
    print("rolled back to", PolicyStore(s.policy_dir).rollback(a.by))
    return 0


def cmd_research(a, s):
    from .research.collect import collect

    out = collect(a.out_dir)
    print(json.dumps(out["regime"], indent=1))
    print("written:", out["path"])
    return 0


def cmd_compile_schemas(a, s):
    from .reflex.schema import write_schemas

    finalists = json.load(open(a.finalists))["finalists"]
    for p in write_schemas(finalists, s.schema_dir):
        print("wrote", p)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="reflexbot")
    p.add_argument("--config", default="config/default.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    x = sub.add_parser("init-policy"); x.add_argument("--by", default="owner"); x.set_defaults(f=cmd_init_policy)
    x = sub.add_parser("backtest", help="walk-forward backtest of the code rules vs holding BTC")
    x.add_argument("--tf", default="4h"); x.add_argument("--symbols"); x.add_argument("--train", type=int, default=240)
    x.add_argument("--test", type=int, default=60); x.add_argument("--out"); x.set_defaults(f=cmd_backtest)
    x = sub.add_parser("model-backtest", help="replay a decision model on post-cutoff data only")
    x.add_argument("--provider", default="jev", choices=["jev", "haiku"]); x.add_argument("--tf", default="1h")
    x.add_argument("--symbols"); x.add_argument("--deadline-ms", type=float, default=5000.0); x.add_argument("--out")
    x.set_defaults(f=cmd_model_backtest)
    x = sub.add_parser("run", help="paper trading loop"); x.add_argument("--once", action="store_true")
    x.add_argument("--poll", type=float, default=60.0); x.set_defaults(f=cmd_run)
    x = sub.add_parser("health"); x.add_argument("--max-age", type=float, default=300.0); x.set_defaults(f=cmd_health)
    x = sub.add_parser("status"); x.set_defaults(f=cmd_status)
    x = sub.add_parser("nightly"); x.add_argument("--loop", action="store_true")
    x.add_argument("--no-tests", action="store_true"); x.set_defaults(f=cmd_nightly)
    x = sub.add_parser("promote", help="HUMAN ONLY: activate a policy that passed the gates")
    x.add_argument("version"); x.add_argument("--by", required=True); x.add_argument("--confirm", required=True)
    x.set_defaults(f=cmd_promote)
    x = sub.add_parser("rollback"); x.add_argument("--by", required=True); x.set_defaults(f=cmd_rollback)
    x = sub.add_parser("research", help="pull dated live market data for the regime read")
    x.add_argument("--out-dir", default="data/research"); x.set_defaults(f=cmd_research)
    x = sub.add_parser("compile-schemas"); x.add_argument("--finalists", default="config/finalists.json")
    x.set_defaults(f=cmd_compile_schemas)

    a = p.parse_args(argv)
    return a.f(a, load_settings(a.config))


if __name__ == "__main__":
    sys.exit(main())
