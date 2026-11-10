"""Append-only SQLite journal: every decision, miss, fill, rejection and equity mark.

The nightly loop reads this and nothing else. Rows are never updated except to
attach the realised outcome to a decision once its horizon has passed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, policy TEXT, model TEXT,
  snapshot TEXT, decision TEXT, p_cal REAL, action TEXT, reasons TEXT,
  latency_ms REAL, px REAL, outcome INTEGER, UNIQUE(ts, symbol, policy));
CREATE TABLE IF NOT EXISTS misses (
  id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, kind TEXT, detail TEXT, latency_ms REAL);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY, ts INTEGER, symbol TEXT, side TEXT, qty REAL, price REAL,
  fee REAL, slippage_bps REAL, order_id TEXT, reason TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS equity (ts INTEGER PRIMARY KEY, equity REAL, cash REAL, positions TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts INTEGER, kind TEXT, detail TEXT);
"""


class Journal:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def decision(self, ts, symbol, policy, model, snapshot, decision, p_cal, action, reasons, latency_ms, px) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO decisions(ts,symbol,policy,model,snapshot,decision,p_cal,action,reasons,latency_ms,px)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ts, symbol, policy, model, json.dumps(snapshot), json.dumps(decision), p_cal, action,
             json.dumps(reasons), latency_ms, px))
        self.db.commit()

    def miss(self, ts, symbol, kind, detail, latency_ms=0.0) -> None:
        self.db.execute("INSERT INTO misses(ts,symbol,kind,detail,latency_ms) VALUES(?,?,?,?,?)",
                        (ts, symbol, kind, detail[:500], latency_ms))
        self.db.commit()

    def fill(self, f, reason: str, mode: str = "paper") -> None:
        self.db.execute(
            "INSERT INTO fills(ts,symbol,side,qty,price,fee,slippage_bps,order_id,reason,mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f.ts, f.symbol, f.side, f.qty, f.price, f.fee, f.slippage_bps, f.order_id, reason, mode))
        self.db.commit()

    def mark(self, ts, equity, cash, positions: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO equity(ts,equity,cash,positions) VALUES(?,?,?,?)",
                        (ts, equity, cash, json.dumps(positions)))
        self.db.commit()

    def event(self, kind: str, detail: str, ts: Optional[int] = None) -> None:
        self.db.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                        (ts or int(time.time() * 1000), kind, detail[:1000]))
        self.db.commit()

    # ----- reads for the nightly loop -----
    def rows(self, table: str, since_ms: int = 0) -> list[dict]:
        if table not in {"decisions", "misses", "fills", "equity", "events"}:
            raise ValueError(table)
        cur = self.db.execute(f"SELECT * FROM {table} WHERE ts >= ? ORDER BY ts", (since_ms,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def resolve_outcomes(self, price_at, horizon_ms: int, now_ms: int) -> int:
        """Attach outcome (1 right / 0 wrong) to directional decisions whose horizon has passed.

        `price_at(symbol, ts_ms)` returns the close at or just after ts, or None.
        """
        from .calibration import direction_outcome

        n = 0
        cur = self.db.execute("SELECT id, ts, symbol, decision, px FROM decisions WHERE outcome IS NULL")
        for id_, ts, symbol, dec, px in cur.fetchall():
            if ts + horizon_ms > now_ms:
                continue
            later = price_at(symbol, ts + horizon_ms)
            if later is None:
                continue
            o = direction_outcome(json.loads(dec)["direction"], px, later)
            if o is None:
                o = -1  # neutral: not scorable
            self.db.execute("UPDATE decisions SET outcome=? WHERE id=?", (o, id_))
            n += 1
        self.db.commit()
        return n
