"""Backtest the lognormal model on settled hourly BTC markets (KXBTCD).

For each past hour H we look at the market `lead` minutes before settlement, using Kalshi's
historical bid/ask candlesticks and Coinbase 1-minute closes, then score the model's
trades against the real outcome. Buys happen at the ask (NO ask = 1 - YES bid) plus fee.

Run:  python -m signal_engine.backtest_btc --hours 48 --cache /path/cache.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from typing import Any, Dict, List

from . import coinbase, kalshi
from .prob import above_prob, realized_sigma

SERIES = "KXBTCD"
WINDOWS = (15, 30, 60, 120)


def _quote_at(cs: List[Dict[str, Any]], t0: int):
    """Latest two-sided (bid, ask) at or before t0 from Kalshi candlesticks."""
    for c in sorted(cs, key=lambda c: c["end_period_ts"], reverse=True):
        if c["end_period_ts"] > t0:
            continue
        ask = kalshi.dollars(c.get("yes_ask", {}).get("close_dollars"))
        bid = kalshi.dollars(c.get("yes_bid", {}).get("close_dollars"))
        if 0 < ask < 1 and 0 < bid < 1 and ask >= bid:
            return bid, ask
    return None


def collect(hours: int, cache_path: str, lead: int = 10, band: float = 0.005) -> List[Dict[str, Any]]:
    cache: Dict[str, Any] = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path))
    now = int(time.time())
    last = (now // 3600) * 3600 - 3600
    closes = [last - 3600 * i for i in range(hours)]
    cb = coinbase.candles("BTC-USD", closes[-1] - 5 * 3600, last + 300)
    start_map = {int(c[0]): c for c in cb}
    ordered = sorted(start_map)

    for H in closes:
        key = str(H)
        if key in cache:
            continue
        t0 = H - lead * 60
        c0 = start_map.get(t0 - 60)
        if not c0:
            cache[key] = []
            continue
        hist = [start_map[t][4] for t in ordered if t <= t0 - 60]
        sig = {}
        for w in WINDOWS:
            if len(hist) >= w:
                sig[str(w)] = realized_sigma(hist[-w:])
        ms = kalshi.markets(SERIES, status="settled", min_close_ts=H - 30, max_close_ts=H + 30)
        spot = c0[4]
        exp = next((kalshi.dollars(m.get("expiration_value")) for m in ms if m.get("expiration_value")), None)
        rows = []
        for m in ms:
            k = m.get("floor_strike")
            if k is None or m.get("result") not in ("yes", "no"):
                continue
            if abs(k / spot - 1) > band:
                continue
            q = _quote_at(kalshi.candlesticks(SERIES, m["ticker"], t0 - 900, t0 + 60), t0)
            time.sleep(0.2)
            if not q:
                continue
            rows.append(dict(H=H, ticker=m["ticker"], K=k, spot=spot, sig=sig, bid=q[0], ask=q[1],
                             yes=m["result"] == "yes", minutes=lead - 0.5, settle=exp))
        cache[key] = rows
        json.dump(cache, open(cache_path, "w"))
        print(f"hour {H}: {len(rows)} strikes", flush=True)
    return [r for rows in cache.values() for r in rows]


def evaluate(rows: List[Dict[str, Any]], window: int, min_ev: float, fee: float = 0.02) -> Dict[str, float]:
    pnls: List[float] = []
    brier_model: List[float] = []
    brier_mkt: List[float] = []
    for r in rows:
        s = r["sig"].get(str(window))
        if s is None:
            continue
        p = above_prob(r["spot"], r["K"], s, r["minutes"])
        mid = (r["bid"] + r["ask"]) / 2
        y = 1.0 if r["yes"] else 0.0
        brier_model.append((p - y) ** 2)
        brier_mkt.append((mid - y) ** 2)
        yes_ev = p - r["ask"] - fee
        no_price = 1.0 - r["bid"]
        no_ev = (1 - p) - no_price - fee
        ev, side, price = max((yes_ev, "YES", r["ask"]), (no_ev, "NO", no_price))
        if ev < min_ev:
            continue
        won = (y == 1.0) if side == "YES" else (y == 0.0)
        pnls.append((1.0 if won else 0.0) - price - fee)
    n = len(pnls)
    mean = sum(pnls) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1) if n > 1 else 0.0
    return dict(window=window, min_ev=min_ev, n=n, mean_pnl=mean,
                se=math.sqrt(var / n) if n > 1 else 0.0,
                hit=(sum(1 for x in pnls if x > -fee - 1e-12 and x > 0) / n) if n else 0.0,
                brier_model=sum(brier_model) / len(brier_model) if brier_model else float("nan"),
                brier_market=sum(brier_mkt) / len(brier_mkt) if brier_mkt else float("nan"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--lead", type=int, default=10)
    a = ap.parse_args()
    rows = collect(a.hours, a.cache, a.lead)
    print(f"\n{len(rows)} (hour, strike) observations across <= {a.hours} hours")
    print("window min_ev     n  mean_pnl/contract   se    brier_model brier_market")
    for w in WINDOWS:
        for ev in (0.03, 0.05, 0.08):
            e = evaluate(rows, w, ev)
            print(f"{e['window']:>6} {e['min_ev']:.2f} {e['n']:>6} {e['mean_pnl']:+14.4f} {e['se']:8.4f} "
                  f"{e['brier_model']:12.4f} {e['brier_market']:12.4f}")


if __name__ == "__main__":
    main()
