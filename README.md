# Trading Intelligence API

FastAPI API for market quotes, multi-model intrinsic valuation (ordinary companies, banks, and REITs), yfinance-based analysis, TradingView MCP technical analysis and screeners, sentiment, news, and backtests.

For external application integration details, endpoint samples, response shapes, and client examples, see [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run

```powershell
uvicorn app.main:app --reload
```

Open the API docs at `http://127.0.0.1:8000/docs`.

## Endpoints

- `GET /health`
- `GET /api/v1/markets/{exchange}/{symbol}/quote`
- `GET /api/v1/markets/{exchange}/{symbol}/analysis?timeframe=1D`
- `GET /api/v1/markets/{exchange}/{symbol}/valuation`
- `GET /api/v1/markets/{exchange}/{symbol}/technical?timeframe=1D&include_multi_timeframe=false`
- `GET /api/v1/markets/{exchange}/{symbol}/breakout-analysis?include_four_hour=false`
- `GET /api/v1/markets/{exchange}/gainers`
- `GET /api/v1/markets/{exchange}/losers`
- `GET /api/v1/markets/{exchange}/bollinger-scan`
- `GET /api/v1/markets/{exchange}/rating-filter`
- `POST /api/v1/backtests/{exchange}/{symbol}`
- `POST /api/v1/backtests/{exchange}/{symbol}/compare`
- `POST /api/v1/backtests/{exchange}/{symbol}/walk-forward`
- `GET /api/v1/sentiment/{symbol}`
- `GET /api/v1/news`
- `GET /api/v1/screener/breakouts`

Legacy stock compatibility is limited to the quote and technical-analysis GET routes. New clients should use the canonical market `/quote` and `/technical` routes directly; the retained aliases are:

- `GET /api/v1/stocks/{symbol}/quote`
- `GET /api/v1/stocks/{symbol}/technicals`

The legacy `GET /api/v1/stocks/{symbol}/fundamentals` and `POST /api/v1/stocks/{symbol}/valuation` are not compatibility aliases. They are retained only to return `501`; in particular, the legacy valuation POST is not an alias for the canonical market valuation GET.

Quote responses are Yahoo-backed and include price, previous close, change, currency, market state, and 52-week high/low when Yahoo provides them.
Analysis responses are calculated locally from yfinance OHLCV history, so `/analysis` does not call TradingView's scanner endpoint. Analysis `price_data` also includes yfinance fast quote metadata such as market cap and 52-week high/low when available. The top-level `valuation_metrics` object reports trailing P/E as the primary ratio, forward P/E separately, and their supporting EPS values. Missing trailing P/E is calculated from current price and positive diluted trailing EPS when possible; unavailable or non-positive inputs remain `null` without failing the analysis response.
Technical responses come from TradingView MCP single-symbol technical analysis and include the provider's indicator objects, market sentiment, stock score when available, and trade setup fields when available. A cached TradingView scanner lookup also adds trailing P/E and 52-week high/low. These reference fields are nullable and do not make `/technical` depend on yFinance.
Breakout analysis is a separate yFinance-backed daily setup model. It assigns an auditable 18-point Breakout Trend Confluence score, applies hard rating gates, and can add a separate 4H confirmation that never changes the daily score. The breakout score is technical and does not call the valuation endpoint or use intrinsic value. Valuation remains available as a separate research endpoint. See [docs/breakout-trend-confluence.md](docs/breakout-trend-confluence.md) for the scoring contract, examples, and frontend migration notes.
Valuation responses use financial statements and a separately refreshed current quote. Ordinary operating companies use an owner-earnings DCF, banks use a residual-income model, and recognized REITs use a distribution-and-NAV model (or its documented distribution-only fallback).

## Markets

Exchange codes are mapped to Yahoo-compatible symbols where needed. For Singapore, use `SGX`:

- Quote: `GET /api/v1/markets/SGX/D05/quote`
- Analysis: `GET /api/v1/markets/SGX/D05/analysis?timeframe=1D`
- Ordinary-company valuation: `GET /api/v1/markets/SGX/S63/valuation`
- Bank valuation: `GET /api/v1/markets/SGX/D05/valuation`
- REIT valuation: `GET /api/v1/markets/SGX/C38U/valuation`
- Technical: `GET /api/v1/markets/SGX/D05/technical?timeframe=1D`
- Breakout analysis: `GET /api/v1/markets/SGX/D05/breakout-analysis?include_four_hour=true`

The provider also accepts Yahoo-style Singapore symbols such as `S63.SI` for valuation and returns public symbols such as `SGX:S63`. The same normalization remains available to analysis and technical routes. Market-wide SGX scanners still depend on the symbol universe available from the TradingView MCP package.

For spot gold, use `TVC` with `XAUUSD`:

- Quote: `GET /api/v1/markets/TVC/XAUUSD/quote`
- Analysis: `GET /api/v1/markets/TVC/XAUUSD/analysis?timeframe=1D`
- Technical: `GET /api/v1/markets/TVC/XAUUSD/technical?timeframe=1D`
- Backtest: `POST /api/v1/backtests/TVC/XAUUSD`

yfinance-backed quote, analysis, and backtest data resolves this to `GC=F`. TradingView MCP technical analysis resolves `XAUUSD` to TradingView's `TVC:GOLD` feed.

## Intrinsic Valuation

The implemented endpoint is:

```http
GET /api/v1/markets/{exchange}/{symbol}/valuation
```

Ordinary-company, bank, and REIT examples:

```text
GET /api/v1/markets/NASDAQ/AAPL/valuation
GET /api/v1/markets/SGX/S63/valuation
GET /api/v1/markets/SGX/D05/valuation
GET /api/v1/markets/SGX/C38U/valuation
```

The API recognizes company type automatically; clients cannot force a model. Ordinary operating companies use `owner_earnings_dcf`; banks with compatible bank statements use `bank_residual_income`; recognized REITs or property trusts use `reit_distribution_nav` when NAV per unit is available, or the lower-confidence `reit_distribution_only` fallback when at least three usable DPU years exist but NAV is unavailable. Classification gives explicit REIT/property-trust metadata priority over broad financial or real-estate labels. A bank needs explicit bank-industry metadata and compatible bank-like statements. Insurers, ETFs and mutual funds, commodities, cryptocurrencies, pre-revenue companies, ambiguous classifications, missing required inputs, incompatible units/currencies, and failed model eligibility return HTTP `200` with `status: "valuation_unreliable"`, explicit `quality.reasons`, and no method or intrinsic-value claim. A failed bank or REIT calculation never falls back to the ordinary-company DCF.

For every usable annual or trailing period, the model calculates:

```text
owner earnings = operating cash flow
               - maintenance capex
               - stock-based compensation
               - interest paid outside operating cash flow
```

Operating cash flow already includes working-capital movements, so working capital is not subtracted again. Total capital expenditure is the current conservative maintenance-capex proxy. Stock-based compensation is treated as an owner expense, and IFRS interest classified in financing is subtracted once. At least three positive annual periods are required and five are preferred. The normalized starting value is the median of at least two available components: trailing owner earnings, a weighted three-year average, and a five-year median owner-earnings margin applied to trailing revenue.

The model projects ten years and fades growth toward the terminal rate:

| Scenario | Starting owner earnings | Initial growth | Required return | Terminal growth |
| --- | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Derived growth minus 4 points, floor -20% | 12% | 2% |
| Base | 100% of normalized | Derived growth, clamped to -15% through 12% | 10% | 2.5% |
| Bull | 105% of normalized | Derived growth plus 3 points, cap 15% | 8% | 3% |

Owner earnings are after interest and are discounted directly to equity value, then divided by current diluted shares. The owner-earnings model does not subtract debt again or add an automatic excess-cash adjustment.

### Bank Residual Income

Banks use common equity and net income attributable to common shareholders rather than ordinary-company owner earnings. For forecast year `t`:

```text
excess return_t = (ROE_t - required return) * beginning common equity_t
ending common equity_t = beginning common equity_t
                       + projected net income_t
                       - projected common dividends_t
intrinsic common equity = current common equity
                        + sum(excess return_t / (1 + required return)^t)
intrinsic value per share = intrinsic common equity / current diluted shares
```

Historical ROE is `net income attributable to common / average beginning and ending common equity`. Historical payout is the absolute common-dividend amount divided by positive net income. The model requires positive current common equity, current diluted shares, and at least three usable annual observations with compatible common equity, common net income, common dividends, diluted shares, currency, and units; five observations are preferred. Invalid equity, share counts, payout outside 0% through 100%, non-finite values, or incompatible units produce `valuation_unreliable` rather than invented inputs.

The normalized ROE and payout ratio are medians of up to the five most recent usable observations. Each scenario projects ten years. Year 1 starts from the scenario ROE factor, ROE fades linearly to the required return in year 10, net income equals beginning common equity times projected ROE, and retained earnings grow common equity after projected dividends:

| Scenario | Starting normalized ROE | Required return |
| --- | ---: | ---: |
| Bear | 90% | 12% |
| Base | 100% | 10% |
| Bull | 105% | 8% |

Because ROE reaches the required return in year 10, excess return converges to zero. Version 1 assigns no terminal excess return after year 10. The response exposes `normalized_roe`, `book_value_per_share`, `payout_ratio`, `usable_years`, and projected book equity under `model_details`.

Optional bank-quality inputs are CET1 ratio, non-performing-loan ratio, loan-loss coverage, and reported regulatory-capital headroom. Missing optional metrics do not block a value, but they are reported in warnings/quality details and cap model confidence at medium. SGX fundamentals are yFinance-backed and also cannot receive high confidence; stale or materially incomplete data can lower confidence further.

Bank deposits, borrowings, and regulatory liquidity are part of the bank's operating and funding model, so bank debt and working capital are not treated as ordinary-company owner-earnings adjustments. Residual income starts from common equity and after-interest common net income; subtracting debt or working-capital movements again would mix incompatible models and double count financing effects.

### REIT Distribution And NAV

REITs use per-unit distributions rather than ordinary-company owner earnings:

```text
intrinsic value per unit = PV of ten years of DPU + PV of terminal NAV per unit
```

The required facts are DPU (or distributable income with units), positive current units, current price, reporting currency, and—when available—NAV per unit. The engine requires at least three usable annual DPU observations and normalizes starting DPU from the median of latest trailing DPU, a weighted three-year DPU, and a five-year median DPU when those independent components are available. Base DPU growth is clamped to -3% through 3%; base NAV growth is clamped to -2% through 2.5%. It projects ten annual distributions, then separately reports present values for distributions and terminal value in `model_details`.

| Scenario | Starting DPU | DPU growth | Required return | NAV growth | Terminal NAV |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Base minus 2 points, floor -5% | 10% | Base minus 1 point, clamped -2% through 2.5% | 85% of projected NAV |
| Base | 100% of normalized | Base, clamped -3% through 3% | 8.5% | Base, clamped -2% through 2.5% | 100% of projected NAV |
| Bull | 105% of normalized | Base plus 2 points, cap 5% | 7% | Base plus 1 point, clamped -2% through 2.5% | 110% of projected NAV |

When NAV is unavailable but DPU history is sufficient, `reit_distribution_only` discounts the ten projected distributions plus a Gordon terminal distribution value. Its terminal distribution growth is 0%, 1.5%, and 2.5% for bear, base, and bull respectively, and its confidence is low. Missing NAV does not become zero. Missing DPU history or non-finite/incompatible required facts returns `valuation_unreliable`; it does not select another model. Optional REIT quality facts—aggregate leverage, interest coverage, occupancy, weighted average lease expiry, recurring property capex, and material currency exposure—appear in provenance/warnings and can lower confidence without blocking an otherwise eligible valuation.

The response reports finite positive `bear`, `base`, and `bull` values ordered from low to high. `margin_of_safety_price` is 75% of base intrinsic value. Price labels use this precedence:

1. `very_expensive` when current price is above bull intrinsic value.
2. `expensive` when current price is above 110% of base intrinsic value.
3. `cheap` when current price is at or below the margin-of-safety price.
4. `fair` otherwise.

`sources` provides field-level provider identifiers, including `current_price: "existing_quote_provider"`. `data_quality` reports `primary_source`, `financials_as_of`, `valuation_as_of`, `next_refresh_at`, `stale`, and `missing_fields`; `price_as_of` is a separate top-level timestamp. `high` confidence requires official primary facts, five usable years, compatible inputs, and no material model conflict; `medium` covers complete three-year results and SGX/yFinance facts; `low` covers supported partial results such as distribution-only REIT valuations, stale data, or material optional-data gaps. U.S. fundamentals use SEC Company Facts first only when `STOCK_API_SEC_USER_AGENT` is configured. Otherwise the endpoint uses yFinance fallback fundamentals, adds a warning, and cannot return high confidence. SGX fundamentals use yFinance, preserve `SGD`, and are capped at medium confidence; missing facts or stale data can lower confidence further.

Normalized fundamentals and intrinsic scenarios refresh once daily by default, while the current quote refreshes independently every five minutes. If a fundamentals refresh fails while a usable cached entry remains inside the stale window, the endpoint returns it with `data_quality.stale: true`, a warning, and low confidence. Without usable cached data, provider failure returns `502`. A `404` is returned only when an upstream provider conclusively identifies the symbol as not found, such as a typed SEC or quote not-found response. yFinance fundamentals failures return `502`, including unresolved symbols that yFinance does not distinguish from other upstream failures.

yFinance is an unofficial, provider-dependent source that may be incomplete, delayed, or unavailable. Production deployments that require guaranteed market-data rights or service levels should replace it through the existing provider boundary with an appropriately licensed source.

Intrinsic valuation is strictly separate from `/technical`: `/valuation` does not consume TradingView indicators, scores, sentiment, or signals, and `/technical` does not consume valuation output. `/analysis` remains a separate yFinance OHLCV and P/E endpoint; `/quote` is the independently refreshed current-price source. The legacy `POST /api/v1/stocks/{symbol}/valuation` remains retired at `501` and is not an alias for this canonical GET.

## Technical Analysis

`/technical` returns the TradingView MCP technical analysis payload for one symbol. Client applications should treat indicator objects as provider-shaped dictionaries and read only the fields they need.

Set `include_multi_timeframe=true` to add TradingView MCP alignment analysis for the fixed `1W`, `1D`, `4h`, `1h`, and `15m` timeframes. The normal `timeframe` parameter continues to control the primary single-timeframe analysis only. Reference-data or multi-timeframe failures preserve the primary technical response and are reported through nullable fields, nested per-timeframe errors, and `warnings`.

TradingView's internal bulk scanner is useful for candidate lists because it fetches multiple symbols in fewer upstream calls, but its raw rows are not equivalent to `/technical`: they do not include the endpoint's stock score, trade setup and quality, ATR augmentation, normalized reference fields, or optional multi-timeframe analysis. This API therefore does not expose a bulk `/technical` route.

## Configuration

```powershell
$env:STOCK_API_DEFAULT_EXCHANGE = "NASDAQ"
$env:STOCK_API_DEFAULT_TIMEFRAME = "1D"
```

Valuation configuration (defaults shown; provide the SEC identity to use SEC fundamentals):

```powershell
$env:STOCK_API_SEC_USER_AGENT = "stock-api your-email@example.com"
$env:STOCK_API_VALUATION_CACHE_TTL_SECONDS = "86400"
$env:STOCK_API_VALUATION_QUOTE_TTL_SECONDS = "300"
$env:STOCK_API_VALUATION_STALE_TTL_SECONDS = "604800"
```

The SEC requires an identifying user agent. If `STOCK_API_SEC_USER_AGENT` is absent, U.S. valuations use the documented yFinance fallback instead of calling SEC endpoints.

Useful TradingView MCP tuning variables:

```powershell
$env:TRADINGVIEW_MCP_CACHE_TTL = "60"
$env:TRADINGVIEW_MCP_STALE_TTL = "21600"
$env:TRADINGVIEW_MCP_RETRY_DELAYS = "1.0,4.0"
$env:TRADINGVIEW_MCP_MAX_INFLIGHT = "2"
$env:TRADINGVIEW_MCP_SOCKET_TIMEOUT = "20"
```

Proxy variables are optional:

```powershell
$env:PROXY_ENABLED = "true"
$env:PROXY_HOST = "p.webshare.io"
$env:PROXY_PORT = "80"
$env:PROXY_USERNAME_PREFIX = "your-prefix"
$env:PROXY_PASSWORD = "your-password"
```

News and sentiment use free Reddit/RSS services by default. Marketaux is used only when configured:

```powershell
$env:MARKETAUX_API_TOKEN = "..."
```

## Tests

Run the mocked test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run optional live provider checks only when explicitly enabled:

```powershell
$env:RUN_LIVE_TRADINGVIEW_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live -q
```

Run the opt-in live ordinary-company and SGX bank valuation smoke tests separately:

```powershell
$env:STOCK_API_SEC_USER_AGENT = "stock-api your-email@example.com"
$env:RUN_LIVE_VALUATION_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_live_valuation.py -q
```

## Deployment

For Render deployment, use:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
