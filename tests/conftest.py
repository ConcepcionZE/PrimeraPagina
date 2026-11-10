import math
import random

import pytest

from reflexbot.types import AccountView, Candle, OrderBook

H = 3_600_000
T0 = 1_790_000_000_000 - (1_790_000_000_000 % H)


def make_candles(n=300, start=100.0, drift=0.0005, vol=0.004, seed=1, t0=T0, tf_ms=H, volume=5000.0):
    rng = random.Random(seed)
    out, px = [], start
    for i in range(n):
        o = px
        c = o * math.exp(drift + rng.gauss(0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        out.append(Candle(t0 + i * tf_ms, o, h, l, c, volume * (1 + rng.random())))
        px = c
    return out


def make_book(mid=100.0, spread_bps=4.0, depth_qty=50.0, ts=0, levels=10):
    half = mid * spread_bps / 2e4
    bids = [(mid - half - i * mid * 0.0002, depth_qty) for i in range(levels)]
    asks = [(mid + half + i * mid * 0.0002, depth_qty) for i in range(levels)]
    return OrderBook(bids, asks, ts)


def flat_account(equity=10_000.0):
    return AccountView(equity, equity, equity, equity)


@pytest.fixture
def candles():
    return make_candles()


@pytest.fixture
def account():
    return flat_account()
