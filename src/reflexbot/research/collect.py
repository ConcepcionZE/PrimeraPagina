"""Research data collector: dated, live, primary-source numbers for the regime read.

Every value is fetched at call time and stamped with the fetch time and its
source URL. Nothing is cached or back-filled; if a source fails, the field is
null and the failure is recorded, never replaced with an old or guessed number.

Sources (public, no keys):
  Kraken spot OHLC (via ccxt)      BTC and ETH trend
  Kraken Futures tickers           perp funding and open interest
  CoinGecko /global, /coins/markets  dominance, supply, FDV, performance
  DefiLlama fees / revenue / chains / active-users / stablecoins
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

CG_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin", "XMR": "monero",
          "AVAX": "avalanche-2", "LINK": "chainlink", "XRP": "ripple", "ADA": "cardano", "DOT": "polkadot",
          "LTC": "litecoin", "BCH": "bitcoin-cash", "DOGE": "dogecoin", "ATOM": "cosmos"}
LLAMA_CHAIN = {"ETH": "Ethereum", "SOL": "Solana", "BNB": "BSC", "AVAX": "Avalanche", "BTC": "Bitcoin"}
SOURCES = {
    "global": "https://api.coingecko.com/api/v3/global",
    "markets": "https://api.coingecko.com/api/v3/coins/markets",
    "stablecoins": "https://stablecoins.llama.fi/stablecoincharts/all",
    "fees": "https://api.llama.fi/overview/fees",
    "chains": "https://api.llama.fi/v2/chains",
    "active_users": "https://api.llama.fi/overview/active-users",
    "perps": "https://futures.kraken.com/derivatives/api/v3/tickers",
}


def _get(url: str, params: dict | None = None, errors: list | None = None):
    try:
        r = requests.get(url, params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"http {r.status_code}")
        return r.json()
    except Exception as e:
        if errors is not None:
            errors.append(f"{url}: {type(e).__name__}: {e}")
        return None


def _ema(vals: list[float], n: int) -> float:
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def trend(closes: list[float]) -> dict:
    last = closes[-1]
    return {"last": last, "vs_ema50_pct": round((last / _ema(closes[-200:], 50) - 1) * 100, 2),
            "vs_ema200_pct": round((last / _ema(closes, 200) - 1) * 100, 2),
            "ret_30d_pct": round((last / closes[-31] - 1) * 100, 2),
            "ret_90d_pct": round((last / closes[-91] - 1) * 100, 2),
            "drawdown_from_1y_high_pct": round((1 - last / max(closes[-365:])) * 100, 2)}


def classify_regime(m: dict) -> dict:
    """Market regime from numbers only. Each rule is listed so the call can be audited."""
    signals: dict[str, int] = {}
    b, e = m.get("btc_trend"), m.get("eth_trend")
    if b:
        signals["btc_below_ema200"] = -1 if b["vs_ema200_pct"] < 0 else 1
        signals["btc_30d"] = -1 if b["ret_30d_pct"] < -5 else 1 if b["ret_30d_pct"] > 5 else 0
    if e:
        signals["eth_below_ema200"] = -1 if e["vs_ema200_pct"] < 0 else 1
    st = m.get("stablecoin_supply_30d_change_pct")
    if st is not None:
        signals["stablecoin_liquidity_30d"] = -1 if st < -1 else 1 if st > 1 else 0
    eb = m.get("eth_btc_30d_change_pct")
    if eb is not None:
        signals["rotation_eth_vs_btc"] = -1 if eb < -3 else 1 if eb > 3 else 0
    f = m.get("perp_funding_annualized_pct", {}).get("BTC")
    if f is not None:
        signals["btc_funding"] = 1 if f > 5 else -1 if f < -5 else 0
    score = sum(signals.values())
    label = "risk_off" if score <= -2 else "risk_on" if score >= 2 else "mixed"
    if b and b["ret_30d_pct"] < -25 and (st or 0) < -5:
        label = "crisis"
    return {"label": label, "score": score, "signals": signals}


def collect(out_dir: str = "data/research", symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XMR", "AVAX", "LINK")) -> dict:
    errors: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)
    out: dict = {"fetched_at_utc": now.isoformat(timespec="seconds"), "sources": SOURCES, "metrics": {}, "assets": {}}
    m = out["metrics"]

    # --- BTC / ETH trend from Kraken daily candles ---
    try:
        from ..exchange.kraken import KrakenData
        from ..state_engine import closed_candles

        kd = KrakenData()
        now_ms = int(time.time() * 1000)
        closes = {}
        for s in ("BTC/USDT", "ETH/USDT"):
            closes[s] = [c.c for c in closed_candles(kd.candles(s, "1d", 720), 86_400_000, now_ms)]
        m["btc_trend"], m["eth_trend"] = trend(closes["BTC/USDT"]), trend(closes["ETH/USDT"])
        ratio = [a / b for a, b in zip(closes["ETH/USDT"][-31:], closes["BTC/USDT"][-31:])]
        m["eth_btc_30d_change_pct"] = round((ratio[-1] / ratio[0] - 1) * 100, 2)
    except Exception as e:
        errors.append(f"kraken candles: {type(e).__name__}: {e}")

    g = _get(SOURCES["global"], errors=errors)
    if g:
        d = g["data"]
        m["total_market_cap_usd"] = d["total_market_cap"]["usd"]
        m["total_market_cap_24h_change_pct"] = round(d["market_cap_change_percentage_24h_usd"], 2)
        m["btc_dominance_pct"] = round(d["market_cap_percentage"]["btc"], 2)
        m["eth_dominance_pct"] = round(d["market_cap_percentage"]["eth"], 2)

    st = _get(SOURCES["stablecoins"], errors=errors)
    if st:
        f = lambda x: x["totalCirculatingUSD"]["peggedUSD"]
        m["stablecoin_supply_usd"] = f(st[-1])
        m["stablecoin_supply_7d_change_pct"] = round((f(st[-1]) / f(st[-8]) - 1) * 100, 2)
        m["stablecoin_supply_30d_change_pct"] = round((f(st[-1]) / f(st[-31]) - 1) * 100, 2)

    t = _get(SOURCES["perps"], errors=errors)
    if t:
        fund, oi = {}, {}
        for x in t.get("tickers", []):
            name = {"PF_XBTUSD": "BTC", "PF_ETHUSD": "ETH", "PF_SOLUSD": "SOL"}.get(x.get("symbol"))
            if name and x.get("markPrice"):
                # Kraken reports funding as an absolute USD amount per contract per hour;
                # dividing by the mark price gives the relative hourly rate (our conversion).
                fund[name] = round(x["fundingRate"] / x["markPrice"] * 24 * 365 * 100, 2)
                oi[name] = round(x["openInterest"] * x["markPrice"])
        m["perp_funding_annualized_pct"] = fund
        m["perp_open_interest_usd_kraken"] = oi

    mk = _get(SOURCES["markets"], {"vs_currency": "usd", "ids": ",".join(CG_IDS[s] for s in symbols if s in CG_IDS),
                                   "price_change_percentage": "7d,30d,1y"}, errors)
    by_id = {x["id"]: x for x in (mk or [])}
    fees = {k: _get(SOURCES["fees"], {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true",
                                      "dataType": k}, errors) for k in ("dailyFees", "dailyHoldersRevenue")}
    fmap = {k: {p["name"]: p for p in (v or {}).get("protocols", [])} for k, v in fees.items()}
    chains = {c["name"]: c for c in (_get(SOURCES["chains"], errors=errors) or [])}
    au = _get(SOURCES["active_users"], {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}, errors)
    users = {p["name"]: p for p in (au or {}).get("protocols", []) if p.get("category") == "Chain"}

    for s in symbols:
        x, ch = by_id.get(CG_IDS.get(s, "")), LLAMA_CHAIN.get(s)
        if not x:
            continue
        mc = x.get("market_cap") or 0
        supply_cap = x.get("max_supply") or x.get("total_supply")
        f30 = (fmap["dailyFees"].get(ch) or {}).get("total30d") if ch else None
        h30 = (fmap["dailyHoldersRevenue"].get(ch) or {}).get("total30d") if ch else None
        f7 = (fmap["dailyFees"].get(ch) or {}).get("total7d") if ch else None
        tvl = (chains.get(ch) or {}).get("tvl") if ch else None
        u = users.get(ch) if ch else None
        out["assets"][s] = {
            "price_usd": x["current_price"], "market_cap_usd": mc,
            "fdv_to_mcap": round(x["fully_diluted_valuation"] / mc, 2) if mc and x.get("fully_diluted_valuation") else None,
            "circulating_pct_of_cap": round(x["circulating_supply"] / supply_cap * 100, 1) if supply_cap else None,
            "chg_7d_pct": x.get("price_change_percentage_7d_in_currency"),
            "chg_30d_pct": x.get("price_change_percentage_30d_in_currency"),
            "chg_1y_pct": x.get("price_change_percentage_1y_in_currency"),
            "drawdown_from_ath_pct": x.get("ath_change_percentage"),
            "chain_tvl_usd": tvl, "mcap_to_tvl": round(mc / tvl, 1) if tvl else None,
            "fees_30d_usd": f30, "holders_revenue_30d_usd": h30,
            "mcap_to_annualized_fees": round(mc / (f30 * 365 / 30)) if f30 else None,
            "mcap_to_annualized_holders_revenue": round(mc / (h30 * 365 / 30)) if h30 else None,
            "fees_7d_vs_30d_run_rate": round((f7 / 7) / (f30 / 30), 2) if f7 and f30 else None,
            "active_addresses_24h": u.get("total24h") if u else None,
            "active_addresses_1y_avg": round(u["average1y"]) if u and u.get("average1y") else None,
            "price_as_of": x.get("last_updated"),
        }
    out["regime"] = classify_regime(m)
    out["errors"] = errors
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"snapshot_{now:%Y-%m-%dT%H%MZ}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    out["path"] = path
    return out
