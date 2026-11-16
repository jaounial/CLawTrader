"""Model-free calibration / longshot-bias test on settled Kalshi markets.

No price model is needed: for every settled market we take the quote `lead` minutes before
close, then ask "if you had bought at the ask (YES at ask, NO at 1 - bid) and paid the fee,
what would you have made, by price bucket?". If prices are efficient, mean P&L is about
minus the fee everywhere. A bucket with positive P&L (and a tight error bar) is a candidate.

Run:  python -m signal_engine.calibration --days 7 --cache /path/cache.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from . import kalshi

FEE = 0.02

# group name -> (series tickers, lead minutes before close, strike band vs settle value or None,
#                candle period in minutes, how many minutes of candles to look back from the lead time)
GROUPS: Dict[str, Tuple[List[str], int, float | None, int, int]] = {
    "BTC hourly": (["KXBTCD"], 10, 0.04, 1, 10),
    "BTC 15m": (["KXBTC15M"], 5, None, 1, 10),
    "ETH 15m": (["KXETH15M"], 5, None, 1, 10),
    "FX 15m": (["KXEURUSD15M", "KXGBPUSD15M", "KXUSDJPY15M", "KXUSDCAD15M", "KXAUDUSD15M", "KXUSDMXN15M"], 5, None, 1, 10),
    "Yields 15m": (["KX2YRRATE15M", "KX5YRRATE15M", "KX10YRRATE15M", "KX30YRRATE15M"], 5, None, 1, 10),
    "Index 15m": (["KXINX15M", "KXNDQ15M", "KXDJIA15M"], 5, None, 1, 10),
    "Index hourly": (["KXINXU", "KXNASDAQ100U"], 10, 0.01, 1, 10),
    # weather: quote ~16h before close, i.e. the morning of the measured day, before the high is known
    "Temp high daily": (["KXHIGHNY", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHCHI", "KXHIGHPHIL", "KXHIGHAUS", "KXHIGHDEN",
                         "KXHIGHTHOU", "KXHIGHTBOS", "KXHIGHTATL", "KXHIGHTDAL", "KXHIGHTOKC", "KXHIGHTSFO",
                         "KXHIGHTMIN", "KXHIGHTSATX", "KXHIGHTLV", "KXHIGHTSEA", "KXHIGHTNOLA"], 960, None, 60, 240),
    "Rain daily": (["KXRAIN"], 960, None, 60, 240),
}

EDGES = [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 1.0]


def _ts(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _quote_at(cs: List[Dict[str, Any]], t0: int):
    for c in sorted(cs, key=lambda c: c["end_period_ts"], reverse=True):
        if c["end_period_ts"] > t0:
            continue
        ask = kalshi.dollars(c.get("yes_ask", {}).get("close_dollars"))
        bid = kalshi.dollars(c.get("yes_bid", {}).get("close_dollars"))
        if 0 < ask < 1 and 0 < bid < 1 and ask >= bid:
            return bid, ask
    return None


def collect(days: int, cache_path: str, groups: Iterable[str]) -> List[Dict[str, Any]]:
    cache: Dict[str, Any] = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    now = int(time.time())
    end = (now // 900) * 900 - 900
    start = end - days * 86400
    for gname in groups:
        series_list, lead, band, period, look = GROUPS[gname]
        by_close: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for s in series_list:
            try:
                ms = kalshi.markets(s, status="settled", min_close_ts=start, max_close_ts=end)
            except Exception as e:  # series may not exist / have no history
                print(f"  {s}: skipped ({e})", flush=True)
                continue
            time.sleep(0.2)
            for m in ms:
                if m.get("result") not in ("yes", "no"):
                    continue
                by_close[_ts(m["close_time"])].append(m)
        if band is not None:  # drop strikes far from where the underlying settled
            for c, ms in by_close.items():
                exp = next((kalshi.dollars(m.get("expiration_value")) for m in ms if m.get("expiration_value")), 0)
                by_close[c] = [m for m in ms if exp and m.get("floor_strike")
                               and abs(m["floor_strike"] / exp - 1) <= band]
        print(f"{gname}: {sum(len(v) for v in by_close.values())} settled markets, {len(by_close)} closes", flush=True)
        for close, ms in sorted(by_close.items()):
            key = f"{gname}|{close}"
            if key in cache:
                continue
            t0 = close - lead * 60
            rows = []
            for i in range(0, len(ms), 100):
                chunk = ms[i:i + 100]
                d = kalshi.get("/markets/candlesticks", {
                    "market_tickers": ",".join(m["ticker"] for m in chunk),
                    "start_ts": t0 - look * 60, "end_ts": t0 + 60, "period_interval": period})
                time.sleep(0.15)
                info = {m["ticker"]: m for m in chunk}
                for x in d.get("markets", []):
                    q = _quote_at(x.get("candlesticks", []), t0)
                    m = info.get(x.get("market_ticker"))
                    if q and m:
                        rows.append(dict(group=gname, series=m["ticker"].split("-")[0], close=close,
                                         ticker=m["ticker"], bid=q[0], ask=q[1], yes=m["result"] == "yes"))
            cache[key] = rows
            json.dump(cache, open(cache_path, "w"))
    return [r for k, rows in cache.items() if k.split("|")[0] in set(groups) for r in rows]


def purchases(rows: Iterable[Dict[str, Any]], fee: float = FEE):
    """Yield (cluster, price, pnl) for buying YES at the ask and NO at 1 - bid."""
    for r in rows:
        cl = (r["group"], r["close"])
        yield cl, r["ask"], (1.0 if r["yes"] else 0.0) - r["ask"] - fee
        no_p = 1.0 - r["bid"]
        yield cl, no_p, (0.0 if r["yes"] else 1.0) - no_p - fee


def bucket_stats(rows: Iterable[Dict[str, Any]], fee: float = FEE, edges: List[float] = EDGES):
    """Per price bucket: n, clusters, avg price, win rate, mean pnl, cluster-robust s.e."""
    cells: Dict[int, Dict[Tuple, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for cl, price, pnl in purchases(rows, fee):
        for b in range(len(edges) - 1):
            if edges[b] <= price < edges[b + 1]:
                cells[b][cl].append((price, pnl))
                break
    out = []
    for b in sorted(cells):
        clusters = cells[b]
        n = sum(len(v) for v in clusters.values())
        tot = sum(p for v in clusters.values() for _, p in v)
        mean = tot / n
        avg_price = sum(pr for v in clusters.values() for pr, _ in v) / n
        g = len(clusters)
        # cluster-robust variance of the mean
        num = sum((sum(p for _, p in v) - mean * len(v)) ** 2 for v in clusters.values())
        se = math.sqrt(num * g / max(g - 1, 1)) / n if g > 1 else float("nan")
        # win rate = mean of (pnl + price + fee), since pnl = win - price - fee
        wr = sum(p + pr + fee for v in clusters.values() for pr, p in v) / n
        out.append(dict(lo=edges[b], hi=edges[b + 1], n=n, clusters=g, price=avg_price,
                        win=wr, mean_pnl=mean, se=se))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--groups", nargs="*", default=list(GROUPS))
    a = ap.parse_args()
    rows = collect(a.days, a.cache, a.groups)
    print(f"\n{len(rows)} market observations, fee {FEE:.2f}\n")
    for g in a.groups:
        gr = [r for r in rows if r["group"] == g]
        if not gr:
            continue
        spr = sum(r["ask"] - r["bid"] for r in gr) / len(gr)
        print(f"== {g}: {len(gr)} markets, avg spread {spr:.3f}")
        print("  price bucket     n  clusters  avg_px  win%   mean_pnl   se    z")
        for b in bucket_stats(gr):
            z = b["mean_pnl"] / b["se"] if b["se"] and b["se"] == b["se"] and b["se"] > 0 else float("nan")
            print(f"  {b['lo']:.2f}-{b['hi']:.2f} {b['n']:6d} {b['clusters']:8d}  {b['price']:.3f} "
                  f"{b['win'] * 100:5.1f} {b['mean_pnl']:+9.4f} {b['se']:.4f} {z:+5.1f}")
        print()


if __name__ == "__main__":
    main()
