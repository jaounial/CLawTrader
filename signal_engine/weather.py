"""Daily-high-temperature probability model for Kalshi temperature markets.

Outcomes are integer degrees F (the NWS climate-report high). A forecast high f is turned into
bin probabilities with a Normal error model: actual ~ N(f - bias_city, sigma), discretised.
"""
from __future__ import annotations

from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

_N = NormalDist()

# series -> (city, lat, lon, tz). Coordinates approximate the NWS climate site named in each
# market's rules (CLINYC, CLILAX, ...). Residual location error is absorbed by the per-city bias.
CITIES: Dict[str, Tuple[str, float, float, str]] = {
    "KXHIGHNY": ("New York", 40.7789, -73.9692, "America/New_York"),
    "KXHIGHLAX": ("Los Angeles", 33.9382, -118.3866, "America/Los_Angeles"),
    "KXHIGHMIA": ("Miami", 25.7933, -80.2906, "America/New_York"),
    "KXHIGHCHI": ("Chicago", 41.7868, -87.7522, "America/Chicago"),
    "KXHIGHPHIL": ("Philadelphia", 39.8721, -75.2411, "America/New_York"),
    "KXHIGHAUS": ("Austin", 30.1945, -97.6699, "America/Chicago"),
    "KXHIGHDEN": ("Denver", 39.8617, -104.6732, "America/Denver"),
    "KXHIGHTHOU": ("Houston", 29.6454, -95.2789, "America/Chicago"),
    "KXHIGHTBOS": ("Boston", 42.3606, -71.0097, "America/New_York"),
    "KXHIGHTATL": ("Atlanta", 33.6367, -84.4281, "America/New_York"),
    "KXHIGHTDAL": ("Dallas", 32.8969, -97.0380, "America/Chicago"),
    "KXHIGHTOKC": ("Oklahoma City", 35.3931, -97.6008, "America/Chicago"),
    "KXHIGHTSFO": ("San Francisco", 37.6190, -122.3750, "America/Los_Angeles"),
    "KXHIGHTMIN": ("Minneapolis", 44.8831, -93.2289, "America/Chicago"),
    "KXHIGHTSATX": ("San Antonio", 29.5337, -98.4698, "America/Chicago"),
    "KXHIGHTLV": ("Las Vegas", 36.0719, -115.1634, "America/Los_Angeles"),
    "KXHIGHTSEA": ("Seattle", 47.4444, -122.3139, "America/Los_Angeles"),
    "KXHIGHTNOLA": ("New Orleans", 29.9933, -90.2581, "America/Chicago"),
}


def bin_prob(strike_type: str, floor: Optional[float], cap: Optional[float], mu: float, sigma: float) -> float:
    """P(integer high falls in the market's bin) under N(mu, sigma), with continuity correction.

    less    : "cap-1 or below"   (cap=49 -> <= 48)
    between : floor..cap inclusive
    greater : "floor+1 or above" (floor=56 -> >= 57)
    """
    def cdf(x: float) -> float:
        return _N.cdf((x - mu) / sigma)

    if strike_type == "less":
        return cdf(cap - 0.5)
    if strike_type == "greater":
        return 1.0 - cdf(floor + 0.5)
    if strike_type == "between":
        return cdf(cap + 0.5) - cdf(floor - 0.5)
    raise ValueError(f"unknown strike_type {strike_type}")


def fit_error_model(history: Dict[str, List[float]], shrink: float = 5.0, sigma_floor: float = 1.5,
                    sigma_cap: float = 6.0, min_n: int = 10) -> Optional[Tuple[Dict[str, float], float]]:
    """Per-city bias (shrunk toward 0) and one pooled sigma from forecast errors (forecast - actual).

    Returns None if there is too little history.
    """
    bias: Dict[str, float] = {}
    sq, n = 0.0, 0
    for city, errs in history.items():
        if not errs:
            continue
        m = sum(errs) / len(errs)
        bias[city] = m * len(errs) / (len(errs) + shrink)
        sq += sum((e - m) ** 2 for e in errs)
        n += len(errs)
    if n < min_n:
        return None
    sigma = (sq / max(n - len(bias), 1)) ** 0.5
    return bias, min(max(sigma, sigma_floor), sigma_cap)
