"""Open-Meteo client (free, no key; non-commercial use) with backoff on 429."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict


def get(host: str, path: str, params: Dict[str, Any], tries: int = 12) -> Dict[str, Any]:
    url = f"https://{host}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "signal-engine"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(min(5 * 2 ** i, 120))
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(min(5 * 2 ** i, 60))
    raise RuntimeError("Open-Meteo rate limited too long")


def daily_max(host: str, lat: float, lon: float, start: str, end: str, tz: str,
              variable: str = "temperature_2m_max") -> Dict[str, float]:
    """{YYYY-MM-DD: value} of a daily variable in Fahrenheit."""
    path = "/v1/forecast"
    d = get(host, path, dict(latitude=lat, longitude=lon, start_date=start, end_date=end,
                             daily=variable, temperature_unit="fahrenheit", timezone=tz))
    return {t: v for t, v in zip(d["daily"]["time"], d["daily"][variable]) if v is not None}


def previous_day_max(lat: float, lon: float, start: str, end: str, tz: str, lead_days: int = 1,
                     model: str | None = None) -> Dict[str, float]:
    """Daily max (F) of the forecast issued `lead_days` days before each hour: a true forecast.

    Unlike the 'historical forecast' archive (which stitches the first hours of each model
    run and behaves like analysis), previous-runs data never uses information from the
    day being forecast beyond what that earlier run knew.
    """
    var = f"temperature_2m_previous_day{lead_days}"
    params: Dict[str, Any] = dict(latitude=lat, longitude=lon, start_date=start, end_date=end,
                                  hourly=var, temperature_unit="fahrenheit", timezone=tz)
    if model:
        params["models"] = model
    d = get("previous-runs-api.open-meteo.com", "/v1/forecast", params)
    key = var if model is None else f"{var}_{model}"
    best: Dict[str, float] = {}
    for t, v in zip(d["hourly"]["time"], d["hourly"].get(key, d["hourly"].get(var, []))):
        if v is None:
            continue
        day = t[:10]
        best[day] = max(best.get(day, -1e9), v)
    return best
