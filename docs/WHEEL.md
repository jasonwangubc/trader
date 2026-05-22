# Wheel-strategy scanner

A built-in scanner for the classic options "wheel":
1. Sell a cash-secured put (CSP) on a stock you'd be happy to own.
2. If assigned, sell a covered call (CC) at or above your cost basis.
3. If called away, return to step 1.

The scanner finds CSPs and CCs that meet a tunable yield / DTE / quality profile,
ranks them, and flags concentration risk via correlation + sector breakdowns.

## What you get

- **Candidate table** — symbol, strike, DTE, premium, annualized yield, OTM cushion, |delta| approximation, open interest, bid-ask spread, capital at risk, and a composite 0-100 score.
- **CSPs and CCs** — CSP candidates pulled from a quality-filtered slice of the screener universe. CC candidates only show up for symbols you already hold in 100-lot multiples (cross-referenced with `positions`).
- **Earnings flagged** — any candidate whose expiry falls after the underlying's next earnings date is marked with ⚠ (we don't exclude — your call).
- **IV ceiling** — candidates with IV above `max_implied_volatility` (default 0.55) are dropped. Above that band, the market is pricing in something you don't want to be the counterparty for.
- **Basket correlation** — tick candidates to build a hypothetical basket. The basket panel shows pairwise 90-day return correlation + sector concentration. Pairs ≥ 0.70 and sectors > 35% are flagged.
- **Current exposure report** — collapsible header panel summarizing your *existing* open CSPs/CCs and held stock with the same correlation + sector analysis.

## Data sources (all free)

| Data            | Source                                                                  |
|-----------------|--------------------------------------------------------------------------|
| Options chains  | `yfinance` (Yahoo Finance — no API key, ~5-15 min delayed)               |
| Implied vol     | yfinance per-contract `impliedVolatility`                                |
| Underlying spot | Most recent screener-pipeline close (`screener_scores.last_close`)       |
| Daily returns   | Existing `daily_bars` table (already populated by the EOD pipeline)      |
| Sector          | yfinance `ticker.info["sector"]` (already harvested into `screener_scores`) |
| Earnings dates  | Existing `earnings_dates` table (synced nightly)                         |

Chain results are cached in-process for 5 minutes to avoid hammering Yahoo when the same expiry is hit across many candidates.

## How scoring works

A composite 0-100 score per candidate. Weights (see `wheel_service._score`):

| Component | Weight | What it measures                                                 |
|-----------|--------|------------------------------------------------------------------|
| Yield     | 0.30   | Bell-curve around ~17% annualized. Trap yields (>30%) score low. |
| Cushion   | 0.20   | OTM % — more cushion = safer.                                    |
| Liquidity | 0.15   | Open interest, log-scaled.                                       |
| Spread    | 0.10   | Bid-ask spread as % of mid — tighter is better.                  |
| Quality   | 0.25   | Underlying's screener `composite_score`.                         |
| Earnings  | -0.15  | Flat penalty if next earnings falls inside the holding window.   |

Total score is clamped 0-100. Candidates whose annualized yield exceeds `max_annualized_yield` (default 50%) are dropped entirely — likely catalysts, not opportunities.

## Quality filter (which underlyings get scanned)

The scanner pulls from `screener_scores`, sorted by `fundamental_score desc` (not `composite_score`, because composite is biased toward momentum breakouts which give already-inflated premiums). Filters applied:

- `last_close >= min_underlying_price` (default $10) — no penny names
- `sector is not None` — filters out micro-cap noise
- `fundamental_score >= 0.30` — has profitability/margin/ROE data
- `composite_score >= min_composite_score` (default 0.30) — soft quality floor
- `earnings_annual_growth >= -5%` — skip outright losers
- Not a TSX symbol — Yahoo's Canadian options coverage is unreliable

Caveats:
- We don't store market cap, so blue-chip-vs-small-cap can't be enforced directly. Future enhancement: add a market-cap field via `yfinance.Ticker.info["marketCap"]` to fully separate "no-worry" names from speculative profitable small-caps.
- The IV ceiling (default 0.55) is doing the heavy lifting until then.

## Correlation analyzer

`correlation_service.correlation_report` computes:

1. **90-day pairwise daily-return correlation** from `daily_bars`. Pairs sorted by `|correlation|` descending.
2. **Sector buckets** — total notional per GICS sector, with % of basket.
3. **Single-name notional** — flags any one symbol over 20% of basket.
4. **Flags** — pairs ≥ 0.70, sectors > 35%, single names > 20%.

Used by:
- `POST /api/wheel/correlation` (basket builder on the page)
- `GET /api/wheel/concentration` (the current-exposure header panel)

## API surface

| Method | Path                          | Notes                                                              |
|--------|-------------------------------|--------------------------------------------------------------------|
| GET    | `/api/wheel/candidates`       | List latest scan results. Query params: `strategy`, `min_score`, `min_annualized_yield`, `max_annualized_yield`, `skip_earnings`, `sector`, `limit`. |
| POST   | `/api/wheel/scan`             | Runs a fresh scan synchronously (typically 5-60s depending on `max_candidates_to_scan`). Request body is `WheelScanConfig`. Wipes prior rows for the user. |
| GET    | `/api/wheel/scan/status`      | Last scan summary (counts, duration, error).                       |
| GET    | `/api/wheel/chain/{symbol}`   | Full puts + calls near 30 DTE for a single symbol.                |
| POST   | `/api/wheel/correlation`      | Body: `{symbols, notionals?, lookback_days?}`.                     |
| GET    | `/api/wheel/concentration`    | Current exposure report (open options + holdings).                 |

## Scan tuning knobs

All defaults in `WheelScanConfig`. Exposed via the **Tune** panel in the UI and via `POST /api/wheel/scan` body.

| Knob                       | Default | What it does                                       |
|----------------------------|---------|----------------------------------------------------|
| `target_dte`               | 30      | Center of the expiry window.                       |
| `dte_tolerance`            | 10      | ± days from `target_dte`.                          |
| `min_annualized_yield`     | 0.10    | Soft floor on annualized yield (display filter).   |
| `max_annualized_yield`     | 0.50    | Drop trap-yield names entirely.                    |
| `target_csp_otm_pct`       | 0.07    | Pick puts ~7% below spot.                          |
| `target_cc_otm_pct`        | 0.05    | Pick calls ~5% above spot (on owned shares).       |
| `min_open_interest`        | 50      | Liquidity floor.                                   |
| `max_bid_ask_spread_pct`   | 0.25    | Spread / mid must be tighter than this.            |
| `min_underlying_price`     | 10.0    | Drop names below $10.                              |
| `max_candidates_to_scan`   | 60      | Cap on underlyings to fetch (scan time grows ~linearly). |
| `max_implied_volatility`   | 0.55    | Drop options with IV above this — likely event vol. |
| `min_composite_score`      | 0.30    | Drop underlyings below this composite score.       |

## What's intentionally *not* in here yet

- **Auto-execution** — Wheel candidates do not become tickets automatically. Use the existing **Options → Log new position** form when you actually open a trade.
- **Real Greek calculations** — `delta_approx` is a Black-Scholes approximation with `r=0, q=0`. Good enough for ranking; do not use for hedging.
- **Roll suggestions** — Future: detect an open CC/CSP within N days of expiry and suggest a same-strike-different-expiry or rolling-down trade.
- **Market-cap filter** — Need to harvest `marketCap` from yfinance ticker info into `screener_scores` to bias toward "no-worry" mega-caps.
- **IV rank / IV percentile** — Today we cap absolute IV. A relative-to-history metric (current IV vs trailing 12-month range) would be a more nuanced version.

## Where the code lives

- [backend/app/db/models.py](../backend/app/db/models.py) — `WheelCandidate`
- [backend/app/services/options_chain_service.py](../backend/app/services/options_chain_service.py) — yfinance chain fetcher + TTL cache
- [backend/app/services/wheel_service.py](../backend/app/services/wheel_service.py) — scanner, scoring, persistence
- [backend/app/services/correlation_service.py](../backend/app/services/correlation_service.py) — correlation + sector concentration
- [backend/app/api/wheel.py](../backend/app/api/wheel.py) — HTTP routes
- [frontend/app/wheel/](../frontend/app/wheel/) — page, scan controls, candidates table, concentration panel
- [frontend/lib/wheel.ts](../frontend/lib/wheel.ts) — shared types + formatters
