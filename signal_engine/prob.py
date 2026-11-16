"""Fair-probability models for price-threshold contracts (driftless lognormal).

above_prob: P(S_T >= K), for "price at expiry above strike" contracts (hourly / 15-min).
touch_below_prob: P(min over [0,T] <= B), for "how low will X get" one-touch contracts
    (reflection principle, zero drift).

sigma is volatility per sqrt(minute) as a fraction (e.g. 0.0006 = 0.06% per sqrt-minute).
minutes is time remaining until the settlement window.
"""
from __future__ import annotations

import math
from typing import Sequence


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def realized_sigma(closes: Sequence[float], minutes_per_bar: float = 1.0) -> float:
    """Std dev of log returns per sqrt(minute) from a series of closes."""
    if len(closes) < 3:
        raise ValueError("need at least 3 closes")
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var / minutes_per_bar)


def above_prob(spot: float, strike: float, sigma: float, minutes: float) -> float:
    """P(price at expiry >= strike)."""
    if minutes <= 0:
        return 1.0 if spot >= strike else 0.0
    s = sigma * math.sqrt(minutes)
    if s <= 0:
        return 1.0 if spot >= strike else 0.0
    d = (math.log(spot / strike) - 0.5 * s * s) / s
    return _phi(d)


def touch_below_prob(spot: float, barrier: float, sigma: float, minutes: float) -> float:
    """P(price ever trades at or below barrier before expiry)."""
    if spot <= barrier:
        return 1.0
    s = sigma * math.sqrt(minutes)
    if s <= 0:
        return 0.0
    return min(1.0, 2.0 * _phi(-math.log(spot / barrier) / s))
