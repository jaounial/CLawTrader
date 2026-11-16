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
