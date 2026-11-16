"""Minimal read-only Kalshi public API client (no key needed, stdlib only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def get(path: str, params: Optional[Dict[str, Any]] = None, tries: int = 7) -> Dict[str, Any]:
    """GET with exponential backoff on 429 (public endpoints have a small rate budget)."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "signal-engine"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(min(2 ** i, 30))
    raise RuntimeError(f"rate limited too long: {path}")


def markets(series: str, **params: Any) -> List[Dict[str, Any]]:
    """All markets in a series, following pagination cursors."""
    out: List[Dict[str, Any]] = []
    cursor = ""
    while True:
        q = dict(series_ticker=series, limit=1000, **params)
        if cursor:
            q["cursor"] = cursor
        d = get("/markets", q)
        out += d.get("markets", [])
        cursor = d.get("cursor") or ""
        if not cursor:
            return out


def candlesticks(series: str, ticker: str, start_ts: int, end_ts: int, period: int = 1) -> List[Dict[str, Any]]:
    d = get(f"/series/{series}/markets/{ticker}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period})
    return d.get("candlesticks", [])


def dollars(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
