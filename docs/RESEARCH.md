# Prediction Market Engine: Initial Research

Date: 2026-11-16

**Method and limits.** Findings come from web search result summaries only. Direct page
fetches (Wealthsimple, Kalshi, Globe and Mail) and live API calls to Kalshi were blocked by
this sandbox's network allowlist (DNS failure and proxy 403). Nothing here was read from a
primary page or an API response. Treat every claim as "reported by search, unverified" until
checked against the primary source.

## 1. What Wealthsimple Predict is

- Standalone app announced 2026-06-18; reported as officially launched around 2026-07-29.
- Powered by **Kalshi** (US exchange). About 4,000 contracts at launch.
- CIRO-approved categories only: **economic indicators, financial markets, climate**.
  Sports, elections and pop culture are not tradable. Some banned markets are reportedly
  *displayed* but not tradable.
- Fee reported as a fixed **US$0.02 per contract bought**.
- Requires KYC; reportedly also an employment/income threshold.
- Provincial eligibility conflicts between sources (one says all of Canada, one says all
  but Ontario and Alberta). Verify on Wealthsimple's own page.
- Polymarket is not approved in Canada. ForecastEx is offered via Interactive Brokers
  Canada, not Wealthsimple.

**Implication for this repo:** Wealthsimple Predict is a retail front-end. No public API
was found for it. The underlying contracts are Kalshi contracts, so Kalshi is the data
source and the model for the engine's domain objects. Order execution through Wealthsimple
would be manual unless an API exists (to be confirmed).

Sources:
- https://newsroom.wealthsimple.com/were-launching-a-regulated-prediction-markets-trading-app-heres-why-and-how
- https://news.kalshi.com/p/wealthsimple-predict-kalshi-canada-prediction-markets
- https://www.cbc.ca/news/business/prediction-markets-wealthsimple-9.7239575
- https://betakit.com/wealthsimple-launches-prediction-markets-app-through-us-exchange-kalshi/
- https://www.nationalobserver.com/2026/07/29/news/wealthsimple-prediction-markets-app-launched
- https://www.theglobeandmail.com/investing/personal-finance/article-trading-on-prediction-markets-from-canada-mind-the-fees/

## 2. Kalshi data and API (reference for the data layer)

Reported by search; verify against https://docs.kalshi.com/llms.txt before hardcoding.

- REST: `GET /trade-api/v2/markets`, `GET /trade-api/v2/markets/{ticker}/orderbook`.
- WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2` (prod),
  `wss://demo-api.kalshi.co/trade-api/ws/v2` (demo). A third-party tutorial cites different
  dedicated hosts, which is unconfirmed.
- Channels: `orderbook_snapshot` + `orderbook_delta`, trades, fills, market status.
- Auth: signed headers (timestamp + method + path) using an API key.
- Open question: whether a Canadian resident can hold a direct Kalshi API account. Several
  sources say Kalshi restricts Canadians or Ontario residents on its own platform.

## 3. Engine design references

| Topic | Takeaway |
|---|---|
| CLOB | Needs market makers; cold start is the weakness. Polymarket moved from AMM to CLOB (late 2022), with off-chain matching and on-chain settlement. |
| LMSR (Hanson) | Always quotes; liquidity parameter `b` sets depth; operator's worst-case loss is bounded at `b*ln(n)`. |
| Hybrid | AMM for thin markets, CLOB once depth grows. A common recommendation. |
| Binary mirroring | A YES bid at p is equivalent to a NO ask at 1-p. Model one book per market, not two. |
| Mint/merge | Matching can split collateral into YES+NO or merge them (Gnosis CTF; oddmaki-core). |

References:
- https://docs.gnosis.io/conditionaltokens/docs/introduction3
- https://risingwave.com/blog/prediction-markets-real-time-architecture/
- https://rocknblock.io/blog/how-polymarket-works-the-tech-behind-prediction-markets
- https://track360.io/blog/prediction-market-liquidity-order-book-amm-parimutuel-2026
- oddmaki-core (Solidity matching engine, unaudited, recent): see https://awesome.ecosyste.ms/projects/github.com%2Foddmaki%2Foddmaki-core
- Not verified: Hanson's original LMSR papers (search by title), Manifold's open-source repo.

## 4. Open-source Kalshi tooling (not code-reviewed)

- https://github.com/rodlaf/KalshiMarketMaker (Avellaneda-Stoikov market maker; author wrote a loss post-mortem)
- https://github.com/JleviEderer/KalshiMarketMaker (has a backtester and settled-market search)
- https://github.com/mablic/Kalshi_Market_Maker
- https://github.com/hamad-khawaja/kalshi-trading-bot
- https://github.com/nikhilnd/kalshi-market-making (university project)
- https://github.com/arshka/pykalshi (client library)
- https://rlafuente.com/posts/2025-3-5-i-lost-150-market-making-on-kalshi

License, maintenance and code quality of all of these are unchecked.

## 5. Evidence on edge (read before designing strategies)

- Favorite-longshot bias on Kalshi: low-priced contracts perform badly, especially for takers
  (Bürgi, Deng, Whelan, "Makers and Takers", https://www.karlwhelan.com/?p=2402).
- Cross-venue calibration study of 292M trades:
  https://arxiv.org/pdf/2602.19520
- Kalshi's own calibration research (publisher bias applies):
  https://kalshi.com/research/kalshi-research-who-calibrates-markets.pdf
- Reported figure: about 70.8% of retail prediction-market traders lose money (single
  secondary source via National Observer; unverified).
- Fee drag: US$0.02 per contract is large on cheap contracts. A 5c contract pays 40% of
  its price in fees on entry.

## 6. Gap between this repo and your goal

`polymarket_hft.py` trades Polymarket BTC contracts from Binance signals. Wealthsimple
Predict does not offer Polymarket, nor BTC-direction markets in the permitted categories as
far as search shows. The existing bot is not reusable as-is. Reusable ideas: the async
structure, `RiskManager`, `PositionManager` and the rate limiter.

## 7. Suggested next steps

1. Decide what "engine" means: (a) analytics/signal engine on Kalshi data for manual trading
   in Wealthsimple, (b) a simulated exchange (matching engine, LMSR/CLOB) for research, or
   (c) both.
2. Unblock primary-source access (allow the Kalshi, Wealthsimple and Globe and Mail hosts),
   then re-verify sections 1 and 2.
3. Build a read-only data collector and paper-trading backtester before any execution code.

## 8. Verified against Kalshi's live public API (2026-11-16 ~16:45Z)

Unlike sections 1-7, these were read directly from `docs.kalshi.com` and
`https://api.elections.kalshi.com/trade-api/v2`.

- **No auth needed** for market data: `/markets`, `/series`, `/events`, orderbook.
  Docs mention `external-api.kalshi.com` as the base, but `api.elections.kalshi.com` serves
  all categories and works.
- **Rate limits:** unauthenticated calls have a separate, smaller budget and return `429`
  with no `Retry-After`. Use exponential backoff and narrow queries (`series_ticker=`).
- **Prices** are dollar strings (`yes_bid_dollars`, `yes_ask_dollars`); contracts pay $1.
  Tick is $0.001 between $0.01 and $0.99.
- **Default `/markets` is full of sports parlays** (`KXMVE...`). Use `mve_filter=exclude`
  and filter by `series_ticker` or category.
- **Short-horizon series exist in Financials** (1,199 series): 15-minute, hourly and daily
  contracts on S&P 500, Nasdaq, Dow, EUR/USD, GBP/USD, USD/JPY, USD/CAD, gold, WTI and
  Treasury yields. Examples: `KXINX15M`, `KXINXU` (hourly above/below ladder),
  `KXEURUSD15M`, `KXUSDJPY15M`, `KX10YRRATE15M`. Crypto has 15-minute/hourly series too.
- **Liquidity snapshot** (one moment, midday ET): `KXINX15M` had 1 open market, spread
  $0.01, open interest 3,416. `KXEURUSD15M` spread $0.02, OI 1,054. `KXUSDJPY15M` spread
  $0.02. `KXINXU` hourly ladder had 200 strikes, most deep ITM/OTM at 0.99/1.00 with
  almost no volume. Only strikes near the money matter.
- **Contract shape:** "will price at expiry be above target?" with a fixed expiry. That is
  directly modelable from underlying price, volatility and time remaining.
- **Cost hurdle:** with a 1-2 cent spread plus the reported 2 cent fee, a trade needs
  roughly 3-4 cents of true edge per contract to profit.

Not yet verified: which of these series Wealthsimple actually lists, its order types and
minimums, the settlement source for each index/FX series, and a real-time price feed for
the underlyings.

## 9. Wealthsimple Predict app vs Kalshi (screenshot check, 2026-11-16 11:43 ET)

User screenshots show the app home screen. Tabs: **Canada, Markets, Companies, Economy,
Climate**. Trending card: "Bank of Canada decision in Dec 2026?", Hike 25bps shown as
Yes 43% / No 57%, "$171K vol", "5 markets".

Cross-check against Kalshi (`KXCBDECISIONCANADA`, closes 2026-12-09):

| Kalshi outcome | yes bid/ask | volume |
|---|---|---|
| Hike 25bps | 0.42 / 0.44 | 64,225 |
| Maintains rate | 0.54 / 0.57 | 99,707 |
| Hike >25bps | 0.00 / 0.01 | 2,738 |
| Cut >25bps | 0.00 / 0.01 | 2,705 |
| Cut 25bps | 0.00 / 0.01 | 1,980 |

- Displayed 43% = midpoint of 0.42/0.44. Total volume 171,355 matches "$171K vol" and the
  five outcomes match "5 markets". **The app mirrors Kalshi's order books.**
- **Alerts must use the ask, not the displayed percentage.** Buying Yes costs 44c, not 43c.
- Trending events are long-dated; short-term 15-minute/hourly markets are presumably under
  the Markets tab but this is not yet confirmed.
- Unknown: order types (limit vs market), minimum size, how the 2c fee is shown.

## 10. App screens and BTC markets (2026-11-16, ~11:46-11:50 ET)

**Wealthsimple Predict app (from screenshots)**
- Markets tab sub-categories seen: Foreign exchange, Head to Head, Rates & Bonds, Stock
  Indices, Commodities, Compute & GPUs, Crypto (more may be off-screen). So FX, indices,
  yields, commodities and **crypto are all listed**.
- Order ticket: enter a **dollar amount** (not a contract count), quick buttons 25/50/75%/Max,
  a USD/CAD toggle, and an order-type dropdown currently reading "Market ($)". Whether
  limit orders exist is unconfirmed. Account balance was $0.00 (unfunded).
- Ticket shows Yes 44c / No 58c, which sum to 102c: these are **ask prices**. "2.27x payout"
  is 1/0.44 and **excludes the 2c fee** (about 2.17x with it).
- Trending long-dated cards (Bank of Canada Dec decision, "Best AI this month" Gemini 87%,
  $573K vol, 7 markets) are the slow end of the product.

**Kalshi BTC series that match the app**
- `KXBTCMINMON` ("How low will BTC get in November?"): one-touch, Below $60,000 at 0.42/0.44,
  matches the app's 44%.
- `KXBTCD` (hourly above/below ladder, strikes every $100): spreads $0.01, 24h volume
  49k-76k contracts per near-the-money strike, open interest 24k-32k. Very liquid.
- `KXBTC15M`: "Yes if the 60s average of CF Benchmarks BRTI before the close is at least the
  60s average before the open". Settlement is BRTI, not Coinbase spot.
- Coinbase public ticker agreed with the ladder's implied centre to within about $50.

**Modelling caveat found the hard way.** A driftless-lognormal model (`signal_engine/prob.py`)
fed 2-hour realized volatility flagged "edges" of 7-12 cents on both ends of the hourly
ladder. Realized vol per sqrt-minute was 0.00063 over 15 min, 0.00075 over 30 min, 0.00106
over 2 h; the output swings with that choice. Those signals were artifacts of the vol input,
not mispricing. **Do not alert on this model until it is backtested** against settled
`KXBTCD` markets with Coinbase 1-minute history.

## 11. Backtest: lognormal model vs settled hourly BTC markets (KXBTCD)

Setup: last 48 settled hours, quote taken 10 minutes before settlement from Kalshi
historical candlesticks, strikes within 0.5% of Coinbase spot, buy at the ask (NO ask = 1 -
YES bid) plus the 2c fee, scored against the real outcome. 308 (hour, strike) observations.
Code: `signal_engine/backtest_btc.py`.

| vol window (min) | min EV | trades | mean P&L / contract | s.e. | Brier model | Brier market |
|---|---|---|---|---|---|---|
| 15 | 0.03 | 66 | -0.093 | 0.036 | 0.110 | 0.105 |
| 15 | 0.08 | 14 | -0.073 | 0.061 | 0.110 | 0.105 |
| 30 | 0.03 | 75 | -0.065 | 0.039 | 0.111 | 0.105 |
| 60 | 0.03 | 104 | -0.077 | 0.028 | 0.113 | 0.105 |
| 120 | 0.03 | 123 | -0.067 | 0.028 | 0.113 | 0.105 |
| 120 | 0.08 | 31 | -0.110 | 0.032 | 0.113 | 0.105 |

(Other rows in the run were similar: all 12 combinations lost 6-12c per contract.)

**Conclusion:** the market's mid-price predicted outcomes better than the model (lower
Brier) and every fee-adjusted configuration lost money. The hourly BTC ladder is efficiently
priced relative to a spot-plus-volatility model. Caveats: only 48 hours and overlapping
strikes within each hour, so observations are correlated; this is "no sign of edge", not a
proof that none exists. Quotes are the candle close, so real fills may differ.

**Decision:** no BTC hourly trade alerts. Alerts stay disabled until a strategy passes a
backtest with a positive fee-adjusted result.

## 12. Model-free calibration test (longshot bias + less crowded markets)

Code: `signal_engine/calibration.py` (+ `tests/test_calibration.py`). Data: 7 days of settled
Kalshi markets up to 2026-11-16 ~17:00Z, quote taken 10 min (hourly) or 5 min (15-min
markets) before close from batch candlesticks. For every market both "buy YES at the ask"
and "buy NO at 1 - bid" are scored, paying the 2c fee, bucketed by price. Standard errors
are clustered by settlement time (strikes within one hour are not independent).
6,358 markets total.

| Group | markets | avg spread | best bucket after fee | worst bucket after fee |
|---|---|---|---|---|
| BTC hourly (strikes within 4%) | 1,900 | 1.1c | 0.65-0.80: +0.9c (z +0.3) | 0.20-0.35: -5.5c |
| BTC 15m | 666 | 0.6c | 0.10-0.20: +4.0c (z +1.1) | 0.20-0.35: -6.3c |
| FX 15m (6 pairs) | 2,360 | 2.0c | 0.35-0.50: -0.8c | 0.50-0.65: -4.9c |
| Yields 15m (2/5/10/30Y) | 880 | 4.6c | 0.90-0.95: -0.3c | 0.35-0.50: -8.0c |
| Index 15m | 126 | 1.5c | n too small | n too small |
| Index hourly | 426 | 2.6c | 0.35-0.50: +1.3c (z +0.2) | 0.65-0.80: -12.5c |

**Longshot bias is real but smaller than the fee.** Longshots (price under 5c) won far less
than their price implies (BTC hourly: 0.6% vs 2.3%; FX 15m: 1.4% vs 3.0%), and favourites
(95c+) won slightly more than priced (BTC hourly 99.3% vs 98.4%, about +0.9c before fee).
The largest favourable pre-fee gap in any bucket was about +1.8c (yields, NO side at
90-95c). The flat 2c fee per contract erases it: the 95-100c bucket in BTC hourly lost
1.1c per contract after fee (z -3.2).

**Less crowded markets are not more exploitable at taker prices.** FX and yield 15-minute
contracts have wider spreads (2c and 4.6c) and every bucket was negative or noise after
fee. The few positive cells (BTC 15m 0.10-0.20, z +1.1) are what ~60 tested buckets produce
by chance.

Caveats: one week only; quotes are candle closes; taker fills only (resting limit orders
could capture part of the spread but this cannot be backtested from candles, and
Wealthsimple's fee treatment of limit orders is unknown); the Index 15m sample is tiny
(US market hours only); a bucket with every outcome identical shows a meaninglessly small
standard error.

**Decision:** no strategy in these groups passes. Alerts remain disabled.

## 13. Slower event markets: Canada survey and weather calibration (2026-11-16)

**Canadian economics on Kalshi is too thin to test.** `KXCBDECISIONCANADA` (Bank of Canada
decision) is the only liquid one: $172K volume on the Dec 2026 meeting, but only one prior
meeting (5 markets) has settled, so there is nothing to backtest. `KXCANOREOY` (overnight
rate, annual) and `KXCANCUTS` (cuts in a year) are about $30K each. A Bank-of-Canada
market gives about 8 decisions a year, i.e. a handful of alerts, not high volume. (A broad
"canad" series search also matched many California series; ignore those.)

**Weather is the most testable slow category.** 145 daily series in Climate and Weather.
Highest-temperature markets for about 20 US cities, 12 strikes per city per day, 24h volume
$5K-72K per city, spreads 1.3-3c; `KXRAIN` (daily rain, 71 markets) trades about $500K a
day at 1.2c spread. History is deep (archive cutoff for settled markets is September).

**Model-free calibration, 30 days, quote 16h before close (morning of the measured day):**
`Temp high daily` 2,409 markets, spread 1.7c; `Rain daily` 500 markets, spread 1.5c.
Same method and 2c fee as section 12.

| Group | best bucket after fee | note |
|---|---|---|
| Temp high daily | 0.50-0.65: +0.2c (z +0.1) | every bucket negative or noise; 95-100c: -1.9c (z -4.3) |
| Rain daily | 0.90-0.95: +3.6c (z +1.7) | n=49; one of ~20 buckets, consistent with chance |

Morning prices are roughly calibrated by price level alone; no price-bucket pattern beats
the fee. This does **not** show the markets are unbeatable: it only shows price alone gives
no edge. A better forecast than the market's (ensemble or NWS point forecast vs the ladder)
is untested and needs forecast data hosts that are not yet allowed (Open-Meteo, NWS).

Code change: `signal_engine/calibration.py` groups now carry a candle period and lookback so
sparse daily markets can use hourly candles.
