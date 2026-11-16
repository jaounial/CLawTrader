"""Backtest: true day-ahead forecasts (Open-Meteo previous-runs) vs Kalshi daily-high markets.

For each settled city-day we take the market quote `LEAD_MIN` minutes before close (morning of
the measured day), build bin probabilities from the previous day's forecast with a bias/sigma
fitted ONLY on days at least 2 days earlier (walk-forward, no look-ahead), and score buying the
mispriced side at the ask plus the 2c fee against the real settled outcome.

Run:  python -m signal_engine.backtest_weather --days 75 --cache /path/wx_cache.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from . import kalshi, openmeteo
from .calibration import _quote_at, _ts
from .weather import CITIES, bin_prob, fit_error_model

LEAD_MIN = 960      # 16h before close = morning of the measured day
FEE = 0.02
TRAIN_DAYS = 30


def _measured_date(rules: str) -> str | None:
    m = re.search(r"for (\w+ \d+), (\d{4})", rules or "")
    if not m:
        return None
    return dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d %Y").date().isoformat()


def _actual(exp: Any) -> float | None:
    """Settled high in F: the number if given, else the midpoint of a 'A° to B°' label."""
    try:
        return float(exp)
    except (TypeError, ValueError):
        m = re.match(r"\s*(-?\d+)\D+(-?\d+)", str(exp or ""))
        return (float(m.group(1)) + float(m.group(2))) / 2 if m else None


def collect(days: int, cache_path: str, cached_only: bool = False) -> Dict[str, Any]:
    cache: Dict[str, Any] = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    now = int(time.time())
    start, end = now - days * 86400, now
    by_close: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    truth: Dict[str, Dict[str, float]] = {}
    for series, (city, lat, lon, tz) in CITIES.items():
        if cached_only and f"fc|{series}" not in cache:
            continue  # no forecast history for this city: leave it out entirely
        mk = f"mk|{series}"
        if mk not in cache:
            ms = kalshi.markets(series, status="settled", min_close_ts=start, max_close_ts=end)
            slim = []
            for m in ms:
                d = _measured_date(m.get("rules_primary", ""))
                if d and m.get("result") in ("yes", "no"):
                    slim.append(dict(ticker=m["ticker"], city=city, date=d, close=_ts(m["close_time"]),
                                     stype=m["strike_type"], floor=m.get("floor_strike"),
                                     cap=m.get("cap_strike"), yes=m["result"] == "yes",
                                     actual=_actual(m.get("expiration_value"))))
            cache[mk] = slim
            json.dump(cache, open(cache_path, "w"))
            time.sleep(0.3)
        for m in cache[mk]:
            by_close[m["close"]].append(m)
            if m["actual"] is not None:
                truth.setdefault(city, {})[m["date"]] = m["actual"]
        fk = f"fc|{series}"
        if fk not in cache and truth.get(city) and not cached_only:
            ds = sorted(truth[city])
            cache[fk] = openmeteo.previous_day_max(lat, lon, ds[0], ds[-1], tz, 1)
            json.dump(cache, open(cache_path, "w"))
            print(f"{city}: {len(ds)} days, forecasts {len(cache[fk])}", flush=True)
            time.sleep(15.0)
    rows: List[Dict[str, Any]] = []
    for close, ms in sorted(by_close.items()):
        key = f"q|{close}"
        if key not in cache:
            t0 = close - LEAD_MIN * 60
            got = []
            for i in range(0, len(ms), 100):
                chunk = ms[i:i + 100]
                d = kalshi.get("/markets/candlesticks", {
                    "market_tickers": ",".join(m["ticker"] for m in chunk),
                    "start_ts": t0 - 240 * 60, "end_ts": t0 + 60, "period_interval": 60})
                time.sleep(0.15)
                info = {m["ticker"]: m for m in chunk}
                for x in d.get("markets", []):
                    q = _quote_at(x.get("candlesticks", []), t0)
                    m = info.get(x.get("market_ticker"))
                    if q and m:
                        got.append({**m, "bid": q[0], "ask": q[1]})
            cache[key] = got
            json.dump(cache, open(cache_path, "w"))
        rows += cache[key]
    fc = {city: cache.get(f"fc|{series}", {}) for series, (city, *_r) in CITIES.items()}
    return dict(rows=rows, fc=fc, truth=truth)


def _fit_for(date: str, fc: Dict[str, Dict[str, float]], truth: Dict[str, Dict[str, float]],
             cache: Dict[str, Any]):
    if date in cache:
        return cache[date]
    d = dt.date.fromisoformat(date)
    lo, hi = (d - dt.timedelta(days=TRAIN_DAYS + 1)).isoformat(), (d - dt.timedelta(days=2)).isoformat()
    hist = {c: [fc[c][k] - truth[c][k] for k in truth[c] if lo <= k <= hi and k in fc.get(c, {})]
            for c in truth}
    cache[date] = fit_error_model(hist)
    return cache[date]


def evaluate(data: Dict[str, Any], min_ev: float, sigma_scale: float = 1.0, fee: float = FEE) -> Dict[str, Any]:
    rows, fc, truth = data["rows"], data["fc"], data["truth"]
    fits: Dict[str, Any] = {}
    cl: Dict[str, List[float]] = defaultdict(list)
    brier_m, brier_k = [], []
    sides = {"YES": [], "NO": []}
    for r in rows:
        f = fc.get(r["city"], {}).get(r["date"])
        fit = _fit_for(r["date"], fc, truth, fits)
        if f is None or fit is None:
            continue
        bias, sigma = fit
        mu = f - bias.get(r["city"], 0.0)
        p = bin_prob(r["stype"], r["floor"], r["cap"], mu, sigma * sigma_scale)
        y = 1.0 if r["yes"] else 0.0
        brier_m.append((p - y) ** 2)
        brier_k.append((((r["bid"] + r["ask"]) / 2) - y) ** 2)
        no_price = 1.0 - r["bid"]
        ev, side, price = max((p - r["ask"] - fee, "YES", r["ask"]), ((1 - p) - no_price - fee, "NO", no_price))
        if ev < min_ev:
            continue
        won = (y == 1.0) if side == "YES" else (y == 0.0)
        pnl = (1.0 if won else 0.0) - price - fee
        cl[r["date"]].append(pnl)
        sides[side].append(pnl)
    allp = [x for v in cl.values() for x in v]
    n = len(allp)
    mean = sum(allp) / n if n else 0.0
    g = len(cl)
    num = sum((sum(v) - mean * len(v)) ** 2 for v in cl.values())
    se = math.sqrt(num * g / max(g - 1, 1)) / n if n and g > 1 else float("nan")
    return dict(min_ev=min_ev, scale=sigma_scale, n=n, days=g, mean=mean, se=se,
                yes_n=len(sides["YES"]), yes=sum(sides["YES"]) / max(len(sides["YES"]), 1),
                no_n=len(sides["NO"]), no=sum(sides["NO"]) / max(len(sides["NO"]), 1),
                brier_model=sum(brier_m) / len(brier_m) if brier_m else float("nan"),
                brier_market=sum(brier_k) / len(brier_k) if brier_k else float("nan"),
                scored=len(brier_m))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=75)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--cached-only", action="store_true",
                    help="only use cities whose forecasts are already cached (no Open-Meteo calls)")
    a = ap.parse_args()
    data = collect(a.days, a.cache, a.cached_only)
    print("cities used:", sorted({r["city"] for r in data["rows"]}))
    errs = [data["fc"][c][d] - data["truth"][c][d] for c in data["truth"] for d in data["truth"][c]
            if d in data["fc"].get(c, {})]
    print(f"\n{len(data['rows'])} quoted markets; forecast-vs-actual: n={len(errs)}, "
          f"MAE={sum(abs(e) for e in errs) / max(len(errs), 1):.2f}F, "
          f"bias={sum(errs) / max(len(errs), 1):+.2f}F")
    print("sigma_scale min_ev  trades  days  mean_pnl   se     z   | YES n/mean   NO n/mean  | brier model/market")
    for sc in (0.9, 1.0, 1.2):
        for ev in (0.03, 0.05, 0.08, 0.12):
            e = evaluate(data, ev, sc)
            z = e["mean"] / e["se"] if e["se"] == e["se"] and e["se"] > 0 else float("nan")
            print(f"{sc:>9.1f} {ev:6.2f} {e['n']:7d} {e['days']:5d} {e['mean']:+9.4f} {e['se']:.4f} {z:+5.1f} | "
                  f"{e['yes_n']:5d} {e['yes']:+.3f}  {e['no_n']:5d} {e['no']:+.3f} | "
                  f"{e['brier_model']:.4f}/{e['brier_market']:.4f} (n={e['scored']})")


if __name__ == "__main__":
    main()
