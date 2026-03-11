# CLawTrader

A Python HFT bot that exploits latency between external BTC price predictions and live [Polymarket](https://polymarket.com) contract prices.

## Strategy

1. Subscribe to a real-time Binance BTC/USDT stream
2. Generate a 15-minute directional forecast using four weighted signals
3. Compare the implied probability to the current Polymarket YES/NO mid-price
4. When the gap exceeds **0.3%**, submit a limit order within **100 ms**
5. Close or cancel the position after it fills or times out (5 min)

---

## Architecture

```
Binance WebSocket ──► PredictionEngine ──► composite implied-prob
                                                    │
Polymarket WebSocket ─► PolymarketClient ──► live market quote
                                                    │
                              ArbitrageDetector ◄───┘
                                      │ edge > 0.3%
                              RiskManager (sizing + circuit-breaker)
                                      │
                              PolymarketClient.place_order()
                                      │
                              PositionManager (timeout + PnL tracking)
```

### Classes

| Class | Role |
|---|---|
| `PredictionEngine` | Binance WS → 4 signals → composite probability |
| `PolymarketClient` | Polymarket WS + CLOB REST, EIP-712 order signing |
| `ArbitrageDetector` | Compares implied-prob vs market mid-price |
| `RegimeDetector` | Linear-regression R² → trending/sideways position scale |
| `RiskManager` | Kelly-inspired sizing, daily loss circuit-breaker |
| `PositionManager` | Tracks open orders, enforces timeouts |
| `RateLimiter` | Token-bucket, configurable orders/second cap |

### Prediction signals (weighted composite)

| Signal | Weight | Source |
|---|---|---|
| MACD momentum (EMA-12/26) | 1.5 | Price history |
| Order-book imbalance (top-3 depth) | 1.0 | Binance depth stream |
| VWAP divergence (20-bar) | 1.2 | Price × volume |
| Volatility-adjusted strike distance | 0.8 | Realized vol vs contract threshold |

---

## Risk controls

| Control | Default | Config key |
|---|---|---|
| Max capital per trade | 0.5 % | `MAX_CAPITAL_PER_TRADE` |
| Daily loss limit | 2 % | `DAILY_LOSS_LIMIT_PCT` |
| Max open positions | 10 | `MAX_OPEN_POSITIONS` |
| Position timeout | 5 min | `POSITION_TIMEOUT_SEC` |
| Sideways market scale | 50 % | `SIDEWAYS_SCALE` |
| Trending market scale | 150 % | `TREND_SCALE` |
| Order target latency | 100 ms | `ORDER_TARGET_MS` |

---

## Requirements

- Python 3.10+
- Polygon wallet with USDC
- Polymarket account + API keys (non-US)

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/CLawTrader.git
cd CLawTrader
pip install -r requirements.txt
```

### 2. Create your `.env`

```bash
cp .env.example .env
```

Fill in `.env`:

```
PRIVATE_KEY=0xYOUR_POLYGON_PRIVATE_KEY
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
YES_TOKEN_ID=        # optional — auto-discovered if blank
NO_TOKEN_ID=         # optional — auto-discovered if blank
INITIAL_CAPITAL=1000
LOG_LEVEL=INFO
```

### 3. Approve the CLOB contract (one-time)

```python
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=POLYGON,
    key="0xYOUR_PRIVATE_KEY",
)
client.set_allowances()
```

### 4. Find target market token IDs

```bash
curl "https://gamma-api.polymarket.com/markets?tag=Bitcoin&active=true&closed=false" \
  | python3 -m json.tool
```

Copy the `token_id` for YES and NO outcomes of your chosen market into `.env`.

### 5. Run

```bash
export $(grep -v '^#' .env | xargs) && python polymarket_hft.py
```

---

## Dry-run mode

Set `INITIAL_CAPITAL=0` in `.env`. The bot runs the full detection pipeline and logs every opportunity it would trade — without placing any orders.

---

## Sample log output

```
2026-03-11 10:00:01.123 │ INFO     │ CLOB credentials valid: True
2026-03-11 10:00:01.456 │ INFO     │ Binance WebSocket connected
2026-03-11 10:00:01.789 │ INFO     │ Polymarket WebSocket connected
2026-03-11 10:00:02.001 │ INFO     │ On-chain USDC balance: $1000.00
2026-03-11 10:00:05.234 │ INFO     │ ORDER → YES │ $5.00 USDC │ px=0.6120 │ edge=0.0041 │ regime=TRENDING_UP
2026-03-11 10:00:05.289 │ INFO     │   ✓ Filled in 55.1 ms │ order=0x3fa891...
────────────────────────────────────────────────────────────────────
2026-03-11 10:01:05.000 │ INFO     │ METRICS  capital=$1001.23  daily_pnl=+1.2300  total_pnl=+1.2300
                                   │   trades=4  win_rate=75.0%  volume=$20.00  errors=0
                                   │   signals=1842  opps=6  skipped=2  open_pos=0
                                   │   exec_avg=61.3 ms  det_avg=0.18 ms  regime=TRENDING_UP
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `PRIVATE_KEY is not set` | Verify `.env` is exported before running |
| `are_api_creds_valid: False` | Regenerate API keys on Polymarket |
| `place_order failed: allowance` | Re-run `client.set_allowances()` |
| No orders firing | Confirm market is active and token IDs are correct |
| `Latency exceeded target` | Normal on cold start; JIT warms up after a few orders |

---

## Extending signals

To plug in a paid data source (CryptoQuant, TradingView webhooks, etc.), add a coroutine to `PredictionEngine`:

```python
async def _my_source_loop(self) -> None:
    # fetch from your API ...
    self._custom_signal = (direction, weight, confidence)  # -1 to 1, float, 0-1
```

Then include it in `_aggregate()` alongside the existing four signals.

---

## Disclaimer

This software is provided for **educational and research purposes only**. Prediction-market and crypto trading involves significant financial risk. You are solely responsible for all trading decisions, losses, and compliance with applicable laws in your jurisdiction. The authors make no warranty of profitability or fitness for any particular purpose.

---

## License

MIT
