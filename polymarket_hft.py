#!/usr/bin/env python3
"""
Polymarket HFT Bot — BTC Prediction Latency Arbitrage
======================================================
Strategy: Monitor 15-minute BTC forecasts from multiple sources.
Detect when predicted probability differs from live Polymarket prices by >0.3%.
Execute limit orders within 100 ms before the market self-corrects.

Architecture:
  - Full async/await with asyncio
  - Binance WebSocket for real-time BTC price + order-book data
  - Polymarket CLOB REST + WebSocket (py-clob-client)
  - Multi-signal prediction engine (momentum, order-flow, vol-adjusted)
  - Market-regime detector (trending vs. sideways position scaling)
  - Token-bucket rate limiter
  - Daily loss circuit-breaker and position-timeout manager
  - Structured logging to console + file with millisecond timestamps

Requirements:
    pip install aiohttp websockets py-clob-client eth-account numpy scipy

Environment variables (see .env.example):
    PRIVATE_KEY, POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE, YES_TOKEN_ID, NO_TOKEN_ID,
    BTC_CONDITION_ID, INITIAL_CAPITAL

DISCLAIMER: This software is for educational and research purposes only.
Prediction-market trading carries substantial financial risk.  You are
solely responsible for all trading decisions and their consequences.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import statistics
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Tuple

import numpy as np

try:
    import aiohttp
except ImportError:
    sys.exit("Missing: pip install aiohttp")

try:
    import websockets
except ImportError:
    sys.exit("Missing: pip install websockets")

try:
    from eth_account import Account
except ImportError:
    sys.exit("Missing: pip install eth-account")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, Side
    from py_clob_client.constants import POLYGON
except ImportError:
    sys.exit("Missing: pip install py-clob-client")


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    # ── Credentials ────────────────────────────────────────────────────────────
    PRIVATE_KEY: str              = os.getenv("PRIVATE_KEY", "")
    POLYMARKET_API_KEY: str       = os.getenv("POLYMARKET_API_KEY", "")
    POLYMARKET_API_SECRET: str    = os.getenv("POLYMARKET_API_SECRET", "")
    POLYMARKET_API_PASSPHRASE: str= os.getenv("POLYMARKET_API_PASSPHRASE", "")

    # ── Target market token IDs (set manually or auto-discovered) ──────────────
    YES_TOKEN_ID: str             = os.getenv("YES_TOKEN_ID", "")
    NO_TOKEN_ID: str              = os.getenv("NO_TOKEN_ID", "")
    BTC_CONDITION_ID: str         = os.getenv("BTC_CONDITION_ID", "")

    # ── Polymarket endpoints ───────────────────────────────────────────────────
    CLOB_HOST: str   = "https://clob.polymarket.com"
    GAMMA_HOST: str  = "https://gamma-api.polymarket.com"
    WS_HOST: str     = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # ── Binance feed ───────────────────────────────────────────────────────────
    BINANCE_WS_BASE: str = "wss://stream.binance.com:9443/ws"

    # ── Strategy thresholds ────────────────────────────────────────────────────
    MIN_EDGE: float           = 0.003   # 0.3 % minimum predicted-vs-market gap
    MAX_EDGE: float           = 0.05    # 5 %  upper bound (above = stale/suspicious)
    SIGNAL_CONFIDENCE_MIN: float = 0.58 # minimum composite signal confidence
    PREDICTION_WINDOW_SEC: int   = 900  # 15-minute forecast horizon

    # ── Risk management ────────────────────────────────────────────────────────
    MAX_CAPITAL_PER_TRADE: float = 0.005   # 0.5 % of total capital per order
    DAILY_LOSS_LIMIT_PCT: float  = 0.02    # 2 %  daily loss circuit-breaker
    MAX_OPEN_POSITIONS: int      = 10
    POSITION_TIMEOUT_SEC: int    = 300     # force-close after 5 min
    SIDEWAYS_SCALE: float        = 0.50    # reduce size 50 % in sideways regime
    TREND_SCALE: float           = 1.50    # increase size 50 % in trending regime

    # ── Execution ─────────────────────────────────────────────────────────────
    ORDER_TARGET_MS: int         = 100     # target detection-to-order latency
    MAX_RETRIES: int             = 2
    RATE_LIMIT_OPS: int          = 50      # max orders/second (conservative)

    # ── Capital ───────────────────────────────────────────────────────────────
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "1000"))

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str  = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str   = "hft_bot.log"
    METRICS_SEC: int = 60


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def build_logger(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("pm_hft")
    logger.setLevel(getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d │ %(levelname)-8s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for handler in (logging.StreamHandler(), logging.FileHandler(cfg.LOG_FILE)):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PredictionSignal:
    """Composite BTC directional signal from all sources."""
    ts: float               # time.monotonic()
    source: str
    btc_now: float          # current BTC/USDT price
    btc_pred: float         # predicted price at T+15 min
    direction: str          # "UP" | "DOWN" | "SIDEWAYS"
    confidence: float       # 0.0 – 1.0
    implied_prob: float     # probability BTC will be above contract threshold
    volatility: float = 0.0

    @property
    def age_ms(self) -> float:
        return (time.monotonic() - self.ts) * 1_000


@dataclass
class MarketQuote:
    """Live YES/NO mid-prices from Polymarket."""
    token_id: str
    yes_px: float           # 0–1 probability
    no_px: float
    spread: float
    ts: float

    @property
    def age_ms(self) -> float:
        return (time.monotonic() - self.ts) * 1_000


@dataclass
class Opportunity:
    """Identified arbitrage edge."""
    ts: float
    quote: MarketQuote
    signal: PredictionSignal
    edge: float             # |implied_prob – market_px|
    side: str               # "YES" | "NO"
    token_id: str
    limit_px: float         # limit price to submit
    confidence: float


@dataclass
class Position:
    order_id: str
    token_id: str
    side: str
    size_usd: float
    entry_px: float
    opened_ts: float
    status: str = "OPEN"    # OPEN | FILLED | CANCELLED
    exit_px: float = 0.0
    pnl: float = 0.0

    @property
    def age_sec(self) -> float:
        return time.monotonic() - self.opened_ts


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    daily_reset_ts: float = field(default_factory=time.monotonic)
    volume: float = 0.0
    errors: int = 0
    signals: int = 0
    opportunities: int = 0
    skipped: int = 0
    exec_times: Deque[float] = field(default_factory=lambda: deque(maxlen=500))

    def log_exec(self, ms: float) -> None:
        self.exec_times.append(ms)

    @property
    def avg_exec_ms(self) -> float:
        return statistics.mean(self.exec_times) if self.exec_times else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    def maybe_reset_daily(self) -> None:
        if time.monotonic() - self.daily_reset_ts > 86_400:
            self.daily_pnl = 0.0
            self.daily_reset_ts = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════════
#  MARKET REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Classifies BTC price action as TRENDING_UP / TRENDING_DOWN / SIDEWAYS / MIXED
    using linear-regression R² and normalised slope on a rolling window.
    Regime drives position-size scaling.
    """

    def __init__(self, lookback: int = 120) -> None:
        self._prices: Deque[float] = deque(maxlen=lookback)
        self._regime = "UNKNOWN"
        self._r2 = 0.0

    def update(self, price: float) -> None:
        self._prices.append(price)
        if len(self._prices) >= 30:
            self._recalc()

    def _recalc(self) -> None:
        y = np.array(self._prices, dtype=float)
        x = np.arange(len(y), dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        y_hat = slope * x + intercept
        ss_res = float(np.sum((y - y_hat) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        norm_slope = abs(slope) / (y.mean() + 1e-9)
        self._r2 = r2
        if r2 > 0.65 and norm_slope > 0.0008:
            self._regime = "TRENDING_UP" if slope > 0 else "TRENDING_DOWN"
        elif r2 < 0.25:
            self._regime = "SIDEWAYS"
        else:
            self._regime = "MIXED"

    @property
    def regime(self) -> str:
        return self._regime

    @property
    def is_trending(self) -> bool:
        return self._regime in ("TRENDING_UP", "TRENDING_DOWN")

    @property
    def is_sideways(self) -> bool:
        return self._regime == "SIDEWAYS"

    def position_scale(self, cfg: Config) -> float:
        if self.is_trending:
            return cfg.TREND_SCALE
        if self.is_sideways:
            return cfg.SIDEWAYS_SCALE
        return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  PREDICTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class PredictionEngine:
    """
    Aggregates four independent BTC directional signals into a single composite
    probability estimate:

      1. MACD-style momentum  (EMA-12 vs EMA-26, weight 1.5)
      2. Order-book imbalance (top-3 bid vs ask depth, weight 1.0)
      3. Volume-weighted trend / VWAP divergence  (weight 1.2)
      4. Volatility-adjusted strike distance      (weight 0.8)

    Data source: Binance combined stream (trade + depth + kline).

    Note: TradingView and CryptoQuant do not offer free public WebSocket APIs.
    Swap out _binance_ws_loop with vendor-specific adapters if you hold paid
    subscriptions, then add additional signals to _aggregate().
    """

    _STREAM = "btcusdt@trade/btcusdt@depth5@100ms/btcusdt@kline_1m"

    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self._cfg = cfg
        self._log = log
        self._prices: Deque[Tuple[float, float]]  = deque(maxlen=600)  # (ts, px)
        self._volumes: Deque[Tuple[float, float]] = deque(maxlen=600)  # (ts, qty)
        self._ob_imbalance: Deque[float]          = deque(maxlen=200)
        self.current_price: float = 0.0
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._binance_ws_loop())
        self._log.info("PredictionEngine started (Binance stream)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    # ── Binance WebSocket ─────────────────────────────────────────────────────

    async def _binance_ws_loop(self) -> None:
        uri = f"{self._cfg.BINANCE_WS_BASE}/{self._STREAM}"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                    self._log.info("Binance WebSocket connected")
                    async for raw in ws:
                        try:
                            await self._ingest(json.loads(raw))
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.warning(f"Binance WS dropped ({exc}), reconnecting …")
                await asyncio.sleep(2)

    async def _ingest(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        ev = msg.get("e")
        now = time.monotonic()

        if ev == "trade":
            px = float(msg["p"])
            qty = float(msg["q"])
            self.current_price = px
            self._prices.append((now, px))
            self._volumes.append((now, qty))

        elif ev == "depthUpdate":
            bids = msg.get("b", [])
            asks = msg.get("a", [])
            if bids and asks:
                bv = sum(float(b[1]) for b in bids[:3])
                av = sum(float(a[1]) for a in asks[:3])
                tot = bv + av
                self._ob_imbalance.append((bv - av) / tot if tot > 0 else 0.0)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_signal(self, strike: float) -> Optional[PredictionSignal]:
        """
        Return a composite PredictionSignal for the given BTC strike price,
        or None if not enough data yet.
        """
        if self.current_price == 0 or len(self._prices) < 50:
            return None

        components: List[Tuple[float, float, float]] = []   # (direction, weight, conf)

        s = self._macd_signal()
        if s:
            components.append(s)

        s = self._ob_signal()
        if s:
            components.append(s)

        s = self._vwap_signal()
        if s:
            components.append(s)

        s = self._strike_distance_signal(strike)
        if s:
            components.append(s)

        if not components:
            return None

        # Weighted aggregation
        total_w = sum(c[1] for c in components)
        if total_w < 1e-9:
            return None

        w_dir  = sum(c[0] * c[1] for c in components) / total_w
        w_conf = sum(c[2] * c[1] for c in components) / total_w

        # Convert directional score to implied probability via logistic shift
        dist_pct = (self.current_price - strike) / (strike + 1e-9)
        base_prob = 1.0 / (1.0 + np.exp(-dist_pct * 20))
        adjustment = w_dir * 0.15 * w_conf          # max ±15 % shift
        implied = float(np.clip(base_prob + adjustment, 0.05, 0.95))

        price_change = w_dir * 0.005 * w_conf       # max ±0.5 % expected move
        pred_price   = self.current_price * (1.0 + price_change)

        direction = "UP" if w_dir > 0.1 else "DOWN" if w_dir < -0.1 else "SIDEWAYS"
        vol = self._realized_vol()

        return PredictionSignal(
            ts=time.monotonic(),
            source="COMPOSITE",
            btc_now=self.current_price,
            btc_pred=pred_price,
            direction=direction,
            confidence=w_conf,
            implied_prob=implied,
            volatility=vol,
        )

    # ── Individual signals ────────────────────────────────────────────────────

    def _macd_signal(self) -> Optional[Tuple[float, float, float]]:
        """EMA-12/26 MACD → direction, weight=1.5"""
        if len(self._prices) < 50:
            return None
        prices = [p[1] for p in self._prices]
        fast = self._ema(prices, 12)
        slow = self._ema(prices, 26)
        if fast is None or slow is None:
            return None
        n = min(len(fast), len(slow))
        macd_line = [fast[i] - slow[i] for i in range(-n, 0)]
        sig_line  = self._ema(macd_line, 9)
        if sig_line is None:
            return None
        histogram = macd_line[-1] - sig_line[-1]
        norm = histogram / (self.current_price + 1e-9)
        direction = float(np.tanh(norm * 1_000))
        confidence = float(min(1.0, abs(norm) * 500))
        return direction, 1.5, confidence

    def _ob_signal(self) -> Optional[Tuple[float, float, float]]:
        """Order-book imbalance → direction, weight=1.0"""
        if len(self._ob_imbalance) < 5:
            return None
        recent = list(self._ob_imbalance)[-10:]
        avg = statistics.mean(recent)
        confidence = float(min(1.0, abs(avg) * 2.0))
        return float(avg), 1.0, confidence

    def _vwap_signal(self) -> Optional[Tuple[float, float, float]]:
        """VWAP divergence → direction, weight=1.2"""
        if len(self._prices) < 20 or len(self._volumes) < 20:
            return None
        recent_px  = [p[1] for p in self._prices][-20:]
        recent_vol = [v[1] for v in self._volumes][-20:]
        tot_vol = sum(recent_vol)
        if tot_vol < 1e-9:
            return None
        vwap = sum(p * v for p, v in zip(recent_px, recent_vol)) / tot_vol
        div  = (self.current_price - vwap) / (vwap + 1e-9)
        direction  = float(np.tanh(div * 100))
        confidence = float(min(1.0, abs(div) * 50))
        return direction, 1.2, confidence

    def _strike_distance_signal(self, strike: float) -> Optional[Tuple[float, float, float]]:
        """Volatility-adjusted distance to strike → direction, weight=0.8"""
        if len(self._prices) < 30 or strike == 0:
            return None
        prices   = [p[1] for p in self._prices][-30:]
        returns  = [np.log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
        vol      = statistics.stdev(returns) if len(returns) > 1 else 0.02
        distance = (self.current_price - strike) / (strike + 1e-9)
        sigma_15 = vol * np.sqrt(15)                # 15-bar horizon
        z_score  = distance / (sigma_15 + 1e-9)
        direction  = float(np.tanh(z_score))
        confidence = float(min(0.9, abs(z_score) / 3.0))
        return direction, 0.8, confidence

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _ema(series: List[float], period: int) -> Optional[List[float]]:
        if len(series) < period:
            return None
        k   = 2.0 / (period + 1)
        ema = [series[0]]
        for v in series[1:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema[period - 1:]

    def _realized_vol(self) -> float:
        if len(self._prices) < 20:
            return 0.02
        prices  = [p[1] for p in self._prices][-20:]
        returns = [abs(np.log(prices[i] / prices[i-1])) for i in range(1, len(prices))]
        return statistics.mean(returns) if returns else 0.02


# ═══════════════════════════════════════════════════════════════════════════════
#  POLYMARKET CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class PolymarketClient:
    """
    Thin async wrapper over the Polymarket CLOB API.

    - WebSocket: real-time price_change / book events
    - REST (via py-clob-client): order creation, cancellation, status, balance
    - py-clob-client handles EIP-712 signing internally given PRIVATE_KEY
    """

    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self._cfg  = cfg
        self._log  = log
        self._clob: Optional[ClobClient] = None
        self._http: Optional[aiohttp.ClientSession] = None
        self._quotes: Dict[str, MarketQuote] = {}
        self._callbacks: List[Callable[[MarketQuote], Coroutine]] = []
        self._ws_task: Optional[asyncio.Task] = None
        self._subscribed: List[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        if not self._cfg.PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY is not set")

        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
        )
        creds = ApiCreds(
            api_key=self._cfg.POLYMARKET_API_KEY,
            api_secret=self._cfg.POLYMARKET_API_SECRET,
            api_passphrase=self._cfg.POLYMARKET_API_PASSPHRASE,
        )
        self._clob = ClobClient(
            host=self._cfg.CLOB_HOST,
            chain_id=POLYGON,
            key=self._cfg.PRIVATE_KEY,
            creds=creds,
        )
        try:
            valid = self._clob.are_api_creds_valid()
            self._log.info(f"CLOB credentials valid: {valid}")
        except Exception as exc:
            self._log.warning(f"Could not validate CLOB credentials: {exc}")

    async def subscribe(self, token_ids: List[str]) -> None:
        self._subscribed = token_ids
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._log.info(f"Subscribing to {len(token_ids)} Polymarket tokens")

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        if self._http:
            await self._http.close()

    def add_quote_callback(self, cb: Callable[[MarketQuote], Coroutine]) -> None:
        self._callbacks.append(cb)

    def latest_quote(self) -> Optional[MarketQuote]:
        return self._quotes.get("YES")

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        while True:
            try:
                async with websockets.connect(
                    self._cfg.WS_HOST, ping_interval=30, ping_timeout=10
                ) as ws:
                    self._log.info("Polymarket WebSocket connected")
                    for tid in self._subscribed:
                        await ws.send(json.dumps({
                            "type": "subscribe",
                            "channel": "price_change",
                            "markets": [tid],
                        }))
                    async for raw in ws:
                        try:
                            await self._on_ws_msg(json.loads(raw))
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.warning(f"Polymarket WS error ({exc}), retrying in 1 s …")
                await asyncio.sleep(1)

    async def _on_ws_msg(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        ev = msg.get("type") or msg.get("event_type") or ""
        now = time.monotonic()

        # price_change event ─────────────────────────────────────────────────
        if ev == "price_change":
            for item in msg.get("changes", [msg]):
                token_id = item.get("asset_id") or item.get("market", "")
                price_s  = item.get("price") or item.get("last_trade_price")
                if not (token_id and price_s):
                    continue
                px = float(price_s)
                key = "YES" if token_id == self._cfg.YES_TOKEN_ID else "NO"
                q = MarketQuote(
                    token_id=token_id,
                    yes_px=px if key == "YES" else 1.0 - px,
                    no_px=1.0 - px if key == "YES" else px,
                    spread=0.0,
                    ts=now,
                )
                self._quotes[key] = q
                for cb in self._callbacks:
                    await cb(q)

        # book / orderbook snapshot ──────────────────────────────────────────
        elif ev in ("book", "orderbook"):
            token_id = msg.get("asset_id", "")
            bids = msg.get("bids", [])
            asks = msg.get("asks", [])
            if not (bids and asks and token_id):
                return
            best_bid = float(bids[0].get("price", 0))
            best_ask = float(asks[0].get("price", 1))
            mid    = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
            key    = "YES" if token_id == self._cfg.YES_TOKEN_ID else "NO"
            q = MarketQuote(
                token_id=token_id,
                yes_px=mid if key == "YES" else 1.0 - mid,
                no_px=1.0 - mid if key == "YES" else mid,
                spread=spread,
                ts=now,
            )
            self._quotes[key] = q
            for cb in self._callbacks:
                await cb(q)

    # ── REST helpers ─────────────────────────────────────────────────────────

    async def rest_midpoint(self, token_id: str) -> Optional[float]:
        """Fallback REST mid-price fetch."""
        try:
            url = f"{self._cfg.CLOB_HOST}/midpoint?token_id={token_id}"
            async with self._http.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    return float(data.get("mid", 0))
        except Exception as exc:
            self._log.debug(f"REST midpoint error: {exc}")
        return None

    async def place_order(
        self,
        token_id: str,
        side: str,      # "BUY"
        price: float,
        size_usd: float,
    ) -> Optional[Dict]:
        """
        Sign and post a GTC limit order.
        size_usd is in USDC; converted to token quantity internally.
        """
        if not self._clob:
            return None
        qty = round(size_usd / (price + 1e-9), 2)
        args = OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=qty,
            side=Side.BUY if side == "BUY" else Side.SELL,
            fee_rate_bps=0,
            nonce=0,
            expiration=0,
        )
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: self._clob.create_and_post_order(args)
            )
        except Exception as exc:
            self._log.error(f"place_order failed: {exc}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        if not self._clob:
            return False
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._clob.cancel({"orderID": order_id})
            )
            return True
        except Exception as exc:
            self._log.error(f"cancel_order {order_id[:12]}… failed: {exc}")
            return False

    async def order_status(self, order_id: str) -> Optional[Dict]:
        if not self._clob:
            return None
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: self._clob.get_order(order_id)
            )
        except Exception:
            return None

    async def usdc_balance(self) -> float:
        try:
            ts  = str(int(time.time() * 1_000))
            msg = ts + "GET" + "/balance-allowance"
            sig = hmac.new(
                self._cfg.POLYMARKET_API_SECRET.encode(),
                msg.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers = {
                "POLY-API-KEY":    self._cfg.POLYMARKET_API_KEY,
                "POLY_SIGNATURE":  sig,
                "POLY_TIMESTAMP":  ts,
                "POLY_ADDRESS":    Account.from_key(self._cfg.PRIVATE_KEY).address,
            }
            url = f"{self._cfg.CLOB_HOST}/balance-allowance"
            async with self._http.get(url, headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    return float(data.get("balance", 0)) / 1e6
        except Exception as exc:
            self._log.debug(f"balance fetch error: {exc}")
        return 0.0

    async def find_btc_markets(self) -> List[Dict]:
        try:
            url = f"{self._cfg.GAMMA_HOST}/markets?tag=Bitcoin&active=true&closed=false"
            async with self._http.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    return data if isinstance(data, list) else []
        except Exception as exc:
            self._log.error(f"find_btc_markets error: {exc}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  RISK MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class RiskManager:
    """
    Enforces:
      - Daily loss circuit-breaker (halts trading when breach detected)
      - Kelly-inspired position sizing scaled by signal confidence, edge, and regime
      - Maximum concurrent open positions
    """

    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self._cfg     = cfg
        self._log     = log
        self.capital  = cfg.INITIAL_CAPITAL
        self.metrics  = Metrics()
        self._halted  = False
        self._halt_reason = ""

    # ── Guards ────────────────────────────────────────────────────────────────

    def trading_allowed(self) -> Tuple[bool, str]:
        self.metrics.maybe_reset_daily()
        if self._halted:
            return False, self._halt_reason
        loss_limit = self.capital * self._cfg.DAILY_LOSS_LIMIT_PCT
        if self.metrics.daily_pnl < -loss_limit:
            self._halted = True
            self._halt_reason = "DAILY_LOSS_LIMIT"
            self._log.critical(
                f"⛔ Daily loss limit reached "
                f"({self.metrics.daily_pnl:+.2f} USDC). Trading halted."
            )
            return False, "DAILY_LOSS_LIMIT"
        return True, ""

    def resume_if_day_rolled(self) -> None:
        self.metrics.maybe_reset_daily()
        if self._halted and self._halt_reason == "DAILY_LOSS_LIMIT":
            if self.metrics.daily_pnl == 0.0:
                self._halted = False
                self._log.info("Daily loss limit reset — trading resumed.")

    # ── Sizing ────────────────────────────────────────────────────────────────

    def position_size(
        self,
        opp: Opportunity,
        open_positions: int,
        regime_scale: float,
    ) -> float:
        allowed, _ = self.trading_allowed()
        if not allowed or open_positions >= self._cfg.MAX_OPEN_POSITIONS:
            return 0.0

        base         = self.capital * self._cfg.MAX_CAPITAL_PER_TRADE
        edge_scale   = min(2.0, opp.edge / (self._cfg.MIN_EDGE + 1e-9))
        size         = base * opp.confidence * edge_scale * regime_scale
        size         = min(size, base)          # hard cap

        return round(size, 2) if size >= 1.0 else 0.0

    # ── Accounting ────────────────────────────────────────────────────────────

    def record_trade(self, pnl: float, size_usd: float) -> None:
        self.metrics.trades  += 1
        self.metrics.total_pnl += pnl
        self.metrics.daily_pnl += pnl
        self.metrics.volume  += size_usd
        self.capital         += pnl
        if pnl > 0:
            self.metrics.wins += 1


# ═══════════════════════════════════════════════════════════════════════════════
#  POSITION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class PositionManager:
    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self._cfg  = cfg
        self._log  = log
        self._open: Dict[str, Position] = {}
        self._lock = asyncio.Lock()

    @property
    def open_count(self) -> int:
        return len(self._open)

    async def open(
        self, order_id: str, token_id: str, side: str, size_usd: float, px: float
    ) -> Position:
        pos = Position(
            order_id=order_id,
            token_id=token_id,
            side=side,
            size_usd=size_usd,
            entry_px=px,
            opened_ts=time.monotonic(),
        )
        async with self._lock:
            self._open[order_id] = pos
        return pos

    async def close(self, order_id: str, exit_px: float) -> Optional[Position]:
        async with self._lock:
            pos = self._open.pop(order_id, None)
        if pos:
            pos.status  = "FILLED"
            pos.exit_px = exit_px
            token_qty   = pos.size_usd / (pos.entry_px + 1e-9)
            pos.pnl     = (exit_px - pos.entry_px) * token_qty if pos.side == "YES" \
                          else (pos.entry_px - exit_px) * token_qty
            self._log.info(
                f"Position closed │ {order_id[:10]}… │ PnL {pos.pnl:+.4f} USDC │ "
                f"side={pos.side} entry={pos.entry_px:.4f} exit={exit_px:.4f}"
            )
        return pos

    async def expired(self) -> List[Position]:
        timeout = self._cfg.POSITION_TIMEOUT_SEC
        async with self._lock:
            return [p for p in self._open.values() if p.age_sec > timeout]


# ═══════════════════════════════════════════════════════════════════════════════
#  ARBITRAGE DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class ArbitrageDetector:
    """
    Compares the composite implied probability from PredictionEngine against
    the live Polymarket mid-price.  Emits an Opportunity when the gap exceeds
    MIN_EDGE and all staleness / sanity checks pass.
    """

    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self._cfg = cfg
        self._log = log
        self._det_times: Deque[float] = deque(maxlen=500)

    def detect(
        self, signal: PredictionSignal, quote: MarketQuote
    ) -> Optional[Opportunity]:
        t0 = time.monotonic()

        # Staleness guards
        if signal.age_ms > 500 or quote.age_ms > 200:
            return None
        if signal.confidence < self._cfg.SIGNAL_CONFIDENCE_MIN:
            return None

        impl  = signal.implied_prob
        yes   = quote.yes_px
        no    = quote.no_px

        yes_edge = impl - yes
        no_edge  = (1.0 - impl) - no

        opp: Optional[Opportunity] = None

        if self._cfg.MIN_EDGE < yes_edge < self._cfg.MAX_EDGE:
            lim_px = yes + yes_edge * 0.5       # conservative limit
            opp = Opportunity(
                ts=t0, quote=quote, signal=signal,
                edge=yes_edge, side="YES",
                token_id=self._cfg.YES_TOKEN_ID,
                limit_px=round(min(lim_px, 0.995), 4),
                confidence=signal.confidence * min(1.0, yes_edge / self._cfg.MIN_EDGE),
            )
        elif self._cfg.MIN_EDGE < no_edge < self._cfg.MAX_EDGE:
            lim_px = no + no_edge * 0.5
            opp = Opportunity(
                ts=t0, quote=quote, signal=signal,
                edge=no_edge, side="NO",
                token_id=self._cfg.NO_TOKEN_ID,
                limit_px=round(min(lim_px, 0.995), 4),
                confidence=signal.confidence * min(1.0, no_edge / self._cfg.MIN_EDGE),
            )

        det_ms = (time.monotonic() - t0) * 1_000
        self._det_times.append(det_ms)
        if opp:
            self._log.debug(
                f"Opportunity │ side={opp.side} edge={opp.edge:.4f} "
                f"conf={opp.confidence:.3f} det={det_ms:.2f} ms"
            )
        return opp

    @property
    def avg_detection_ms(self) -> float:
        return statistics.mean(self._det_times) if self._det_times else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITER  (token bucket)
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self, rate: int) -> None:
        self._rate   = float(rate)
        self._tokens = float(rate)
        self._last   = time.monotonic()
        self._lock   = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now     = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last   = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# ═══════════════════════════════════════════════════════════════════════════════
#  HFT BOT  (main orchestrator)
# ═══════════════════════════════════════════════════════════════════════════════

class HFTBot:
    """
    Orchestrates every subsystem.

    Main data flow:
      Binance WS → PredictionEngine.get_signal()
              ↓
      Polymarket WS → _on_quote_update()
              ↓
      ArbitrageDetector.detect()
              ↓
      RiskManager.position_size()
              ↓
      PolymarketClient.place_order()
              ↓
      PositionManager.open()
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg     = cfg
        self._log     = build_logger(cfg)
        self._pred    = PredictionEngine(cfg, self._log)
        self._clob    = PolymarketClient(cfg, self._log)
        self._det     = ArbitrageDetector(cfg, self._log)
        self._risk    = RiskManager(cfg, self._log)
        self._pos     = PositionManager(cfg, self._log)
        self._regime  = RegimeDetector()
        self._rl      = RateLimiter(cfg.RATE_LIMIT_OPS)
        self._strike: float = 0.0          # BTC price threshold for the contract
        self._running = False

    # ── Startup ───────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._log.info("═" * 60)
        self._log.info("  Polymarket HFT Bot — BTC Prediction Arbitrage")
        self._log.info("═" * 60)

        await self._clob.initialize()

        if not self._cfg.YES_TOKEN_ID:
            await self._auto_discover_market()

        if self._cfg.YES_TOKEN_ID:
            await self._clob.subscribe([
                self._cfg.YES_TOKEN_ID,
                self._cfg.NO_TOKEN_ID,
            ])

        self._clob.add_quote_callback(self._on_quote_update)
        await self._pred.start()

        bal = await self._clob.usdc_balance()
        if bal > 0:
            self._risk.capital = bal
            self._log.info(f"On-chain USDC balance: ${bal:.2f}")
        else:
            self._log.info(f"Using configured capital: ${self._risk.capital:.2f}")

        self._log.info(
            f"Strategy │ min_edge={self._cfg.MIN_EDGE*100:.1f}% "
            f"max_pos={self._cfg.MAX_CAPITAL_PER_TRADE*100:.1f}% "
            f"daily_limit={self._cfg.DAILY_LOSS_LIMIT_PCT*100:.1f}%"
        )
        self._log.info(f"Strike price: ${self._strike:,.0f}")

    # ── Market discovery ──────────────────────────────────────────────────────

    async def _auto_discover_market(self) -> None:
        self._log.info("Searching for BTC markets …")
        markets = await self._clob.find_btc_markets()
        if not markets:
            self._log.warning("No markets found — set YES_TOKEN_ID / NO_TOKEN_ID manually.")
            return
        self._log.info(f"Found {len(markets)} BTC market(s):")
        for i, m in enumerate(markets[:5]):
            self._log.info(f"  {i+1}. {m.get('question','N/A')[:90]}")

        market = markets[0]
        for tok in market.get("tokens", []):
            outcome = tok.get("outcome", "").lower()
            if outcome == "yes":
                self._cfg.YES_TOKEN_ID = tok.get("token_id", "")
            elif outcome == "no":
                self._cfg.NO_TOKEN_ID  = tok.get("token_id", "")

        self._parse_strike(market.get("question", ""))
        self._log.info(
            f"Auto-selected: {market.get('question','')[:80]}  "
            f"strike=${self._strike:,.0f}"
        )

    def _parse_strike(self, text: str) -> None:
        for pat in (r"\$([0-9]{2,}[,0-9]*)", r"above\s+([0-9,]+)", r"over\s+([0-9,]+)"):
            m = re.search(pat, text, re.I)
            if m:
                try:
                    self._strike = float(m.group(1).replace(",", ""))
                    return
                except ValueError:
                    pass

    # ── Core loop callback ────────────────────────────────────────────────────

    async def _on_quote_update(self, quote: MarketQuote) -> None:
        if not self._running or self._strike == 0:
            return
        try:
            if self._pred.current_price > 0:
                self._regime.update(self._pred.current_price)

            signal = self._pred.get_signal(self._strike)
            if not signal:
                return
            self._risk.metrics.signals += 1

            opp = self._det.detect(signal, quote)
            if not opp:
                return
            self._risk.metrics.opportunities += 1

            await self._execute(opp)
        except Exception as exc:
            self._log.error(f"quote handler error: {exc}")
            self._risk.metrics.errors += 1

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute(self, opp: Opportunity) -> None:
        t0 = time.monotonic()

        if not await self._rl.acquire():
            self._risk.metrics.skipped += 1
            return

        allowed, reason = self._risk.trading_allowed()
        if not allowed:
            self._log.warning(f"Trading blocked: {reason}")
            return

        scale = self._regime.position_scale(self._cfg)
        size  = self._risk.position_size(opp, self._pos.open_count, scale)
        if size <= 0:
            self._risk.metrics.skipped += 1
            return

        self._log.info(
            f"ORDER → {opp.side:<3} │ ${size:.2f} USDC │ px={opp.limit_px:.4f} │ "
            f"edge={opp.edge:.4f} │ conf={opp.confidence:.3f} │ "
            f"regime={self._regime.regime}"
        )

        resp = await self._clob.place_order(
            token_id=opp.token_id,
            side="BUY",
            price=opp.limit_px,
            size_usd=size,
        )

        elapsed_ms = (time.monotonic() - t0) * 1_000
        self._risk.metrics.log_exec(elapsed_ms)

        if resp:
            order_id = resp.get("orderID") or resp.get("id", f"local_{int(t0*1000)}")
            await self._pos.open(order_id, opp.token_id, opp.side, size, opp.limit_px)
            emoji = "✓" if elapsed_ms <= self._cfg.ORDER_TARGET_MS else "⚠"
            self._log.info(
                f"  {emoji} Filled in {elapsed_ms:.1f} ms │ order={order_id[:14]}…"
            )
            if elapsed_ms > self._cfg.ORDER_TARGET_MS:
                self._log.warning(
                    f"  Latency exceeded target "
                    f"({elapsed_ms:.0f} ms > {self._cfg.ORDER_TARGET_MS} ms)"
                )
            self._risk.metrics.trades += 1
        else:
            self._log.error("  Order API returned no response")
            self._risk.metrics.errors += 1

    # ── Background tasks ──────────────────────────────────────────────────────

    async def _position_monitor(self) -> None:
        while self._running:
            try:
                for pos in await self._pos.expired():
                    self._log.warning(
                        f"Position timeout: {pos.order_id[:12]}… "
                        f"open {pos.age_sec:.0f} s — cancelling"
                    )
                    q = self._clob.latest_quote()
                    if q:
                        exit_px = q.yes_px if pos.side == "YES" else q.no_px
                        closed  = await self._pos.close(pos.order_id, exit_px)
                        if closed:
                            self._risk.record_trade(closed.pnl, closed.size_usd)
                    await self._clob.cancel_order(pos.order_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.error(f"position monitor error: {exc}")
            await asyncio.sleep(1)

    async def _metrics_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.METRICS_SEC)
            self._risk.resume_if_day_rolled()
            m = self._risk.metrics
            self._log.info("─" * 68)
            self._log.info(
                f"METRICS  capital=${self._risk.capital:.2f}  "
                f"daily_pnl={m.daily_pnl:+.4f}  total_pnl={m.total_pnl:+.4f}"
            )
            self._log.info(
                f"  trades={m.trades}  win_rate={m.win_rate:.1%}  "
                f"volume=${m.volume:.2f}  errors={m.errors}"
            )
            self._log.info(
                f"  signals={m.signals}  opps={m.opportunities}  "
                f"skipped={m.skipped}  open_pos={self._pos.open_count}"
            )
            self._log.info(
                f"  exec_avg={m.avg_exec_ms:.1f} ms  "
                f"det_avg={self._det.avg_detection_ms:.2f} ms  "
                f"regime={self._regime.regime}"
            )
            self._log.info("─" * 68)

    # ── Run / shutdown ────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._running = True
        try:
            await self.initialize()
            self._log.info("Bot running — press Ctrl-C to stop")
            await asyncio.gather(
                asyncio.create_task(self._position_monitor()),
                asyncio.create_task(self._metrics_loop()),
            )
        except asyncio.CancelledError:
            self._log.info("Shutdown signal received")
        except Exception as exc:
            self._log.critical(f"Fatal: {exc}\n{traceback.format_exc()}")
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        self._running = False
        self._log.info("Shutting down …")
        open_ids = list(self._pos._open.keys())
        if open_ids:
            self._log.info(f"Cancelling {len(open_ids)} open position(s)")
            for oid in open_ids:
                await self._clob.cancel_order(oid)
        await self._pred.stop()
        await self._clob.close()
        m = self._risk.metrics
        self._log.info("═" * 60)
        self._log.info("  FINAL SUMMARY")
        self._log.info(f"  Trades   : {m.trades}")
        self._log.info(f"  Win rate : {m.win_rate:.1%}")
        self._log.info(f"  Total PnL: {m.total_pnl:+.4f} USDC")
        self._log.info(f"  Volume   : ${m.volume:.2f} USDC")
        self._log.info(f"  Capital  : ${self._risk.capital:.2f} USDC")
        self._log.info("═" * 60)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    cfg = Config()
    bot = HFTBot(cfg)

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    def _stop() -> None:
        if main_task:
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(_main())
