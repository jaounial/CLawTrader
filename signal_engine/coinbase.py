"""Minimal Coinbase Exchange public market-data client (no key needed, stdlib only)."""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

BASE = "https://api.exchange.coinbase.com"


def _get(path: str, params: Dict[str, Any] | None = None) -> Any:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "signal-engine"})
    for i in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:  # type: ignore[name-defined]
            if e.code != 429:
                raise
            time.sleep(2 ** i)
    raise RuntimeError(f"rate limited: {path}")


import urllib.error  # noqa: E402  (kept after use for readability of _get)


def ticker(product: str = "BTC-USD") -> float:
    return float(_get(f"/products/{product}/ticker")["price"])


def _iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candles(product: str, start_ts: int, end_ts: int, granularity: int = 60) -> List[List[float]]:
    """Candles as [start_ts, low, high, open, close, volume], oldest first (max 300 per request)."""
    out: Dict[int, List[float]] = {}
    t = start_ts
    while t < end_ts:
        e = min(end_ts, t + 300 * granularity)
        rows = _get(f"/products/{product}/candles",
                    {"granularity": granularity, "start": _iso(t), "end": _iso(e)})
        for r in rows:
            out[int(r[0])] = r
        t = e
        time.sleep(0.2)
    return [out[k] for k in sorted(out)]
