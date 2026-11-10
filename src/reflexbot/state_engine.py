"""State engine: live feeds -> one compact numeric snapshot.

Rules this module enforces:
- Causality. A snapshot for time T is built only from candles that closed at or
  before T. Passing a later candle raises; it is never silently used.
- All arithmetic (mid, spread, imbalance, volatility, inventory, exposure,
  drawdown) happens here, in code. The REFLEX never computes a number.
- Data health is checked before any order: missing data, stale feeds, exchange
  errors and clock skew each produce a named issue, and any issue blocks orders.
- The snapshot is the only thing the REFLEX sees and must stay under 400 tokens.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional

from .types import AccountView, Candle, OrderBook

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MIN_BARS = 60
MAX_SNAPSHOT_TOKENS = 400


class CausalityError(ValueError):
    """Raised when data from the future reaches a snapshot."""


@dataclass(frozen=True)
class HealthConfig:
    max_candle_lag_bars: float = 1.5      # last closed candle may be at most this many bars old
    max_book_age_s: float = 15.0
    max_clock_skew_ms: float = 2000.0
    max_spread_bps: float = 80.0
    max_gap_bars: int = 0                 # missing candles tolerated in the lookback window


def closed_candles(candles: list[Candle], tf_ms: int, now_ms: int) -> list[Candle]:
    """Drop the candle that is still forming (and anything stamped in the future)."""
    return [c for c in candles if c.ts + tf_ms <= now_ms]


def health_issues(
    candles: list[Candle],
    tf_ms: int,
    now_ms: int,
    book: Optional[OrderBook],
    server_time_ms: Optional[int],
    cfg: HealthConfig = HealthConfig(),
    exchange_error: Optional[str] = None,
    require_book: bool = True,
) -> list[str]:
    """Return every reason this data must not be traded on. Empty list = healthy."""
    issues: list[str] = []
    if exchange_error:
        issues.append(f"exchange_error:{exchange_error}")
    if len(candles) < MIN_BARS:
        issues.append(f"missing_data:only_{len(candles)}_candles")
        return issues
    window = candles[-MIN_BARS:]
    for c in window:
        vals = (c.o, c.h, c.l, c.c, c.v)
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals) or min(c.o, c.h, c.l, c.c) <= 0:
            issues.append("missing_data:bad_candle_values")
            break
    gaps = sum(1 for a, b in zip(window, window[1:]) if b.ts - a.ts != tf_ms)
    if gaps > cfg.max_gap_bars:
        issues.append(f"missing_data:{gaps}_candle_gaps")
    last_close = candles[-1].ts + tf_ms
    if last_close > now_ms:
        issues.append("causality:last_candle_not_closed")
    if now_ms - last_close > cfg.max_candle_lag_bars * tf_ms:
        issues.append(f"stale_feed:candles_{int((now_ms - last_close) / 1000)}s_old")
    if server_time_ms is None:
        issues.append("clock_skew:no_server_time")
    elif abs(server_time_ms - now_ms) > cfg.max_clock_skew_ms:
        issues.append(f"clock_skew:{int(server_time_ms - now_ms)}ms")
    if book is None:
        if require_book:
            issues.append("missing_data:no_order_book")
    else:
        if not book.bids or not book.asks:
            issues.append("missing_data:empty_order_book")
        else:
            bid, ask = book.bids[0][0], book.asks[0][0]
            if bid >= ask:
                issues.append("exchange_error:crossed_book")
            elif (ask - bid) / ((ask + bid) / 2) * 1e4 > cfg.max_spread_bps:
                issues.append("liquidity:spread_too_wide")
        if (now_ms - book.ts) / 1000 > cfg.max_book_age_s:
            issues.append(f"stale_feed:book_{int((now_ms - book.ts) / 1000)}s_old")
    return issues


# ---------- indicators (pure functions over closed candles) ----------

def _ema(values: list[float], n: int) -> float:
    k = 2.0 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes: list[float], n: int = 14) -> float:
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:], closes[-n:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: list[Candle], n: int = 14) -> float:
    trs = []
    for prev, cur in zip(candles[-n - 1:], candles[-n:]):
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    return sum(trs) / len(trs)


def _realized_vol_annual(closes: list[float], tf_ms: int, n: int = 48) -> float:
    rets = [math.log(b / a) for a, b in zip(closes[-n - 1:], closes[-n:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    bars_per_year = 365 * 86_400_000 / tf_ms
    return math.sqrt(var * bars_per_year) * 100.0


def _efficiency_ratio(closes: list[float], n: int = 24) -> float:
    window = closes[-n - 1:]
    path = sum(abs(b - a) for a, b in zip(window, window[1:]))
    return abs(window[-1] - window[0]) / path if path > 0 else 0.0


def book_stats(book: OrderBook, depth: int = 10) -> dict[str, float]:
    bid, ask = book.bids[0][0], book.asks[0][0]
    mid = (bid + ask) / 2
    bq = sum(q for _, q in book.bids[:depth])
    aq = sum(q for _, q in book.asks[:depth])
    return {
        "mid": mid,
        "spread_bps": (ask - bid) / mid * 1e4,
        "imbalance": (bq - aq) / (bq + aq) if (bq + aq) > 0 else 0.0,
        "depth_quote": (bq + aq) * mid,
    }


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def build_snapshot(
    symbol: str,
    tf: str,
    candles: list[Candle],
    as_of_ms: int,
    account: AccountView,
    book: Optional[OrderBook] = None,
    thesis: Optional[dict] = None,
) -> dict:
    """Build the REFLEX state for `symbol` as of `as_of_ms`.

    `candles` must contain only candles closed at or before `as_of_ms`.
    """
    tf_ms = TF_MS[tf]
    if len(candles) < MIN_BARS:
        raise ValueError(f"need {MIN_BARS} closed candles, got {len(candles)}")
    if candles[-1].ts + tf_ms > as_of_ms:
        raise CausalityError(
            f"candle closing at {candles[-1].ts + tf_ms} is after snapshot time {as_of_ms}"
        )
    if book is not None and book.ts > as_of_ms:
        raise CausalityError("order book is stamped after snapshot time")

    closes = [c.c for c in candles]
    last = closes[-1]
    a = atr(candles)
    vols = [c.v for c in candles[-25:-1]]
    vmean = sum(vols) / len(vols)
    vstd = math.sqrt(sum((v - vmean) ** 2 for v in vols) / len(vols)) or 1.0
    hi = max(c.h for c in candles[-48:])
    lo = min(c.l for c in candles[-48:])
    ema_fast, ema_slow = _ema(closes[-60:], 12), _ema(closes[-60:], 48)
    prices = {symbol: last}
    pos = account.positions.get(symbol)

    snap: dict = {
        "sym": symbol,
        "tf": tf,
        "t": as_of_ms // 1000,
        "px": last,
        "ret1_pct": _r((last / closes[-2] - 1) * 100),
        "ret6_pct": _r((last / closes[-7] - 1) * 100),
        "ret24_pct": _r((last / closes[-25] - 1) * 100),
        "ema_gap_pct": _r((ema_fast / ema_slow - 1) * 100),
        "rsi14": _r(_rsi(closes), 1),
        "atr_pct": _r(a / last * 100),
        "vol_ann_pct": _r(_realized_vol_annual(closes, tf_ms), 1),
        "trend_eff": _r(_efficiency_ratio(closes)),
        "range_pos": _r((last - lo) / (hi - lo) if hi > lo else 0.5),
        "volume_z": _r((candles[-1].v - vmean) / vstd),
        "px_dd_pct": _r((1 - last / max(c.h for c in candles[-MIN_BARS:])) * 100),
        # account state, computed in code
        "inventory_pct": _r((pos.qty * last / account.equity * 100) if pos and account.equity > 0 else 0.0),
        "exposure_pct": _r(account.exposure(prices) / account.equity * 100 if account.equity > 0 else 0.0),
        "acct_dd_pct": _r(account.drawdown * 100),
        "day_loss_pct": _r(account.daily_loss * 100),
        "open_pos": len(account.positions),
    }
    if book is not None and book.bids and book.asks:
        bs = book_stats(book)
        snap["mid"] = bs["mid"]
        snap["spread_bps"] = _r(bs["spread_bps"])
        snap["imbalance"] = _r(bs["imbalance"])
    else:
        snap["mid"] = None
        snap["spread_bps"] = None
        snap["imbalance"] = None
    if thesis:
        snap["thesis"] = {
            "bias": thesis.get("bias", "long"),
            "invalid_below": thesis.get("invalid_below"),
            "above_invalidation": bool(last > thesis["invalid_below"]) if thesis.get("invalid_below") else None,
        }
    n = estimate_tokens(snap)
    if n >= MAX_SNAPSHOT_TOKENS:
        raise ValueError(f"snapshot is {n} tokens (estimate), limit is {MAX_SNAPSHOT_TOKENS}")
    return snap


def estimate_tokens(snapshot: dict) -> int:
    """Conservative token estimate: 1 token per 2.5 characters of compact JSON.

    Numeric JSON tokenizes worse than prose, so this deliberately over-counts.
    Jev's real usage for the same payload is lower (see docs/ARCHITECTURE.md).
    """
    return math.ceil(len(json.dumps(snapshot, separators=(",", ":"))) / 2.5)
