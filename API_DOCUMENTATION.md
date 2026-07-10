# Trading Intelligence API Documentation

This document is for client applications that want to consume the Trading Intelligence API. The API provides quotes, yfinance-based analysis, TradingView MCP technical analysis and market screeners, backtests, sentiment, and news.

## Base URL

Local development:

```text
http://127.0.0.1:8000
```

Production:

```text
https://your-api-host.example.com
```

All primary application endpoints are versioned under:

```text
/api/v1
```

Interactive OpenAPI documentation is available at:

```text
/docs
```

The raw OpenAPI schema is available at:

```text
/openapi.json
```

## Authentication

The current API does not require authentication. If the API is exposed publicly, put it behind an API gateway, reverse proxy, or application-level authentication before sharing it with third-party clients.

## Content Type

For `POST` endpoints, send JSON:

```http
Content-Type: application/json
Accept: application/json
```

## Common Concepts

### Exchange And Symbol

Most endpoints use:

```text
/api/v1/{resource}/{exchange}/{symbol}
```

Examples:

```text
NASDAQ / TSLA
NYSE / IBM
SGX / D05
TVC / XAUUSD
BINANCE / BTCUSDT
```

The exchange is passed through to the provider layer as a market or venue code. Analysis maps symbols to Yahoo-compatible symbols; TradingView MCP technical analysis maps symbols to TradingView-compatible symbols; market-wide screeners still depend on TradingView MCP support.

### Special Symbol Handling

Singapore stocks:

```text
GET /api/v1/markets/SGX/D05/quote
GET /api/v1/markets/SGX/D05/analysis?timeframe=1D
GET /api/v1/markets/SGX/D05/valuation
GET /api/v1/markets/SGX/D05/technical?timeframe=1D
POST /api/v1/backtests/SGX/D05
```

Internally, Singapore quote, analysis, valuation, and backtest data uses Yahoo-style `.SI` symbols such as `D05.SI`. TradingView MCP technical analysis strips that suffix and returns TradingView symbols such as `SGX:D05`.

Spot gold:

```text
GET /api/v1/markets/TVC/XAUUSD/quote
GET /api/v1/markets/TVC/XAUUSD/analysis?timeframe=1D
GET /api/v1/markets/TVC/XAUUSD/technical?timeframe=1D
POST /api/v1/backtests/TVC/XAUUSD
```

Internally, yfinance-backed quote, analysis, and backtest data resolves `XAUUSD` to `GC=F`. TradingView MCP technical analysis resolves `XAUUSD` to TradingView's `TVC:GOLD` feed.

### Timeframes

Use `timeframe` for analysis, technical analysis, and screeners.

Supported values:

```text
5m
15m
1h
4h
1D
1W
1M
```

Recommended defaults:

```text
1D  daily view
4h  swing or active trading view
1h  intraday view
1W  longer trend view
```

For yfinance-backed analysis, `4h` is built by fetching `1h` candles and resampling them into four-hour candles.

### Backtest Intervals

Backtests use `interval`, not `timeframe`.

Supported values:

```text
1d
1h
```

Supported periods:

```text
1mo
3mo
6mo
1y
2y
```

Supported strategies:

```text
rsi
bollinger
macd
ema_cross
supertrend
donchian
rsi_pullback
keltner_breakout
triple_ema
```

`rsi_pullback` and `triple_ema` need enough history for SMA200 warmup. Prefer `period: "1y"` or `period: "2y"` for those strategies.

## Endpoint Summary

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/` | API metadata and docs link |
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/markets/{exchange}/{symbol}/quote` | Latest quote |
| `GET` | `/api/v1/markets/{exchange}/{symbol}/analysis` | yfinance analysis and indicators |
| `GET` | `/api/v1/markets/{exchange}/{symbol}/valuation` | Model-routed owner-earnings or bank residual-income valuation |
| `GET` | `/api/v1/markets/{exchange}/{symbol}/technical` | TradingView MCP technical analysis |
| `GET` | `/api/v1/markets/{exchange}/gainers` | Market gainers |
| `GET` | `/api/v1/markets/{exchange}/losers` | Market losers |
| `GET` | `/api/v1/markets/{exchange}/bollinger-scan` | Bollinger width scan |
| `GET` | `/api/v1/markets/{exchange}/rating-filter` | Technical rating filter |
| `POST` | `/api/v1/backtests/{exchange}/{symbol}` | Run one strategy backtest |
| `POST` | `/api/v1/backtests/{exchange}/{symbol}/compare` | Compare strategies |
| `POST` | `/api/v1/backtests/{exchange}/{symbol}/walk-forward` | Walk-forward validation |
| `GET` | `/api/v1/sentiment/{symbol}` | Sentiment summary |
| `GET` | `/api/v1/news` | News summary |

## Health And Metadata

### Root

```http
GET /
```

Example response:

```json
{
  "name": "Trading Intelligence API",
  "docs": "/docs",
  "health": "/health"
}
```

### Health

```http
GET /health
```

Example response:

```json
{
  "status": "ok"
}
```

## Market Data

### Quote

```http
GET /api/v1/markets/{exchange}/{symbol}/quote
```

Path parameters:

| Name | Required | Description |
| --- | --- | --- |
| `exchange` | yes | Market or venue code, such as `NASDAQ`, `SGX`, `TVC` |
| `symbol` | yes | Symbol, such as `TSLA`, `D05`, `XAUUSD` |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/markets/NASDAQ/TSLA/quote"
```

Example response:

```json
{
  "symbol": "TSLA",
  "exchange": "NASDAQ",
  "price": 428.11,
  "previous_close": 423.19,
  "change": 4.92,
  "change_percent": 1.16,
  "currency": "USD",
  "market_state": "REGULAR",
  "fifty_two_week_high": 555.45,
  "fifty_two_week_low": 349.2,
  "source": "Yahoo Finance",
  "timestamp": "2026-07-09T00:00:00+00:00",
  "warnings": []
}
```

### Analysis

```http
GET /api/v1/markets/{exchange}/{symbol}/analysis?timeframe=1D
```

Query parameters:

| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | Analysis timeframe |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/markets/SGX/D05/analysis?timeframe=1D"
```

Example response shape:

```json
{
  "symbol": "SGX:D05",
  "exchange": "sgx",
  "timeframe": "1D",
  "timestamp": "real-time",
  "price_data": {
    "current_price": 70.02,
    "open": 69.5,
    "high": 70.3,
    "low": 69.2,
    "close": 70.02,
    "previous_close": 69.1,
    "change_percent": 1.33,
    "volume": 1234567,
    "market_cap": 102300000000,
    "fifty_two_week_high": 76.8,
    "fifty_two_week_low": 58.4,
    "yahoo_symbol": "D05.SI"
  },
  "valuation_metrics": {
    "trailing_pe": 12.48,
    "forward_pe": 11.92,
    "diluted_eps_ttm": 5.61,
    "forward_eps": 5.87,
    "primary_pe": "trailing",
    "pe_calculated": false
  },
  "rsi": {},
  "macd": {},
  "sma": {},
  "ema": {},
  "bollinger_bands": {},
  "atr": {},
  "market_sentiment": {
    "overall_rating": 0,
    "buy_sell_signal": "NEUTRAL"
  }
}
```

The analysis response is calculated locally from yfinance OHLCV history and can include many technical indicator objects. `price_data.market_cap`, `price_data.fifty_two_week_high`, and `price_data.fifty_two_week_low` come from yfinance fast quote metadata when available; market cap falls back to shares times latest price when Yahoo omits the direct value.

`valuation_metrics.trailing_pe` is the primary P/E. The API uses Yahoo's finite, positive trailing P/E when available and otherwise calculates current price divided by finite, positive diluted trailing EPS. `valuation_metrics.forward_pe` remains a separately labeled estimate and is calculated from forward EPS only when Yahoo omits the direct ratio. Zero, negative, missing, NaN, or infinite P/E and EPS inputs produce `null`; forward P/E is never substituted for trailing P/E. Fundamental metadata is cached using `STOCK_API_CACHE_TTL_SECONDS` and is best-effort, so a metadata failure leaves nullable valuation fields without failing the rest of `/analysis`.

### Intrinsic Valuation

```http
GET /api/v1/markets/{exchange}/{symbol}/valuation
```

This canonical GET automatically classifies the security and selects the model; clients cannot force a method or supply assumptions. Ordinary operating companies use `owner_earnings_dcf`, eligible banks use `bank_residual_income`, and eligible REITs/property trusts use `reit_distribution_nav` or its documented `reit_distribution_only` fallback.

U.S. and SGX examples:

```bash
curl "http://127.0.0.1:8000/api/v1/markets/NASDAQ/AAPL/valuation"
curl "http://127.0.0.1:8000/api/v1/markets/SGX/S63/valuation"
curl "http://127.0.0.1:8000/api/v1/markets/SGX/D05/valuation"
curl "http://127.0.0.1:8000/api/v1/markets/SGX/C38U/valuation"
```

For SGX, both bare and Yahoo-style symbols are accepted. For example, `D05` and `D05.SI` both normalize provider access to `D05.SI`, return public symbol `SGX:D05`, and preserve `SGD` for price and per-share values. The same rule applies to REIT symbols such as `C38U` and `C38U.SI`, which return `SGX:C38U`.

Current company-type behavior:

| Detected type | Current method | Result |
| --- | --- | --- |
| Ordinary operating company | `owner_earnings_dcf` | Three finite positive intrinsic values when inputs are eligible |
| Bank | `bank_residual_income` | Three finite positive intrinsic values when bank inputs are eligible |
| REIT or property trust with usable DPU and NAV | `reit_distribution_nav` | Three finite positive intrinsic values |
| REIT or property trust with usable DPU but no NAV | `reit_distribution_only` | Lower-confidence finite values using a Gordon terminal distribution |
| Unsupported or ambiguous | none | HTTP `200`, `status: "valuation_unreliable"` |

Classification uses explicit security/quote type, sector and industry, issuer/filing classification, and compatible statement structure; issuer-name patterns are only supporting evidence. REIT/property-trust evidence takes priority over a broad financial or real-estate label. A bank requires explicit bank-industry metadata plus compatible bank-like statements. Banks and REITs are recognized so they cannot accidentally enter owner-earnings DCF. A failed bank or REIT calculation never falls back to owner earnings. Insurers, ETFs, mutual funds, commodities, cryptocurrencies, pre-revenue companies, incompatible currencies/units, ambiguous classifications, and insufficient required facts return the non-numerical `valuation_unreliable` result. It sets `method`, `confidence`, `intrinsic_value`, and `model_details` to `null`, sets `quality.eligible` to `false`, and gives explicit reasons in `quality.reasons` and/or `data_quality.missing_fields`.

#### Owner-Earnings Method

For each usable annual or trailing period:

```text
owner earnings = operating cash flow
               - maintenance capex
               - stock-based compensation
               - interest paid classified outside operating cash flow
```

Operating cash flow already contains working-capital movements, so working capital is not subtracted again. Total capital expenditure is the current conservative maintenance-capex proxy. Capex outflows are normalized to a positive deduction, stock-based compensation is treated as an owner expense, and IFRS interest classified in financing is subtracted exactly once. Under U.S. GAAP, interest already included in operating cash flow has a zero final adjustment.

At least three positive annual owner-earnings periods are required and five are preferred. The normalized starting value is the median of at least two available components:

1. Latest trailing-twelve-month owner earnings.
2. Weighted three-year owner earnings using weights 1, 2, and 3 from oldest to newest.
3. Five-year median owner-earnings margin multiplied by trailing revenue.

Growth is derived from comparable per-share revenue and owner-earnings history. Each scenario projects ten years, fades growth linearly toward its terminal rate, discounts the annual owner earnings, adds the discounted terminal value, and divides the resulting equity value by current diluted shares. Owner earnings are already after interest; debt is not subtracted again, and the owner-earnings model does not add automatic excess cash.

| Scenario | Starting owner earnings | Initial growth | Required return | Terminal growth |
| --- | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Base minus 4 percentage points, floor -20% | 12% | 2% |
| Base | 100% of normalized | Derived growth, clamped to -15% through 12% | 10% | 2.5% |
| Bull | 105% of normalized | Base plus 3 percentage points, cap 15% | 8% | 3% |

The terminal formula is `OE10 * (1 + terminal_growth) / (required_return - terminal_growth)`. Every numerical result must be finite and positive and satisfy `bear <= base <= bull`.

#### Bank Residual-Income Method

Banks are valued from common book equity and after-interest net income attributable to common shareholders:

```text
historical ROE_t = net income attributable to common_t
                 / average(beginning common equity_t, ending common equity_t)
historical payout_t = abs(common dividends_t) / positive common net income_t

projected net income_t = beginning common equity_t * projected ROE_t
projected dividends_t = normalized payout ratio * projected net income_t
excess return_t = (projected ROE_t - required return) * beginning common equity_t
ending common equity_t = beginning common equity_t
                       + projected net income_t
                       - projected dividends_t

intrinsic common equity = current common equity
                        + sum(excess return_t / (1 + required return)^t)
intrinsic value per share = intrinsic common equity / current diluted shares
```

Required inputs are positive current common equity, current diluted shares, and at least three usable annual observations with compatible common equity, net income attributable to common, common dividends, diluted shares, currency, and units; five usable observations are preferred. Normalized ROE and payout are the medians of up to the five most recent usable observations. Payout must remain between 0% and 100%; invalid payout, equity, shares, units, or non-finite values produce `valuation_unreliable` instead of being clamped or replaced with zero.

All scenarios project ten years. Before the fade begins, bear ROE is 90% of normalized ROE, base is 100%, and bull is 105%. ROE fades linearly from that starting assumption to the scenario required return in year 10:

| Scenario | Starting ROE factor | Required return |
| --- | ---: | ---: |
| Bear | 90% | 12% |
| Base | 100% | 10% |
| Bull | 105% | 8% |

The annual calculation discounts excess return before adding retained earnings to the next year's common equity. When year-10 ROE reaches the required return, excess return is zero; version 1 assigns no persistent terminal excess return after year 10. The public `model_details` reports `normalized_roe`, `book_value_per_share`, `payout_ratio`, `usable_years`, ten annual projected book-equity values for each scenario, and nullable CET1, non-performing-loan, and loan-loss-coverage ratios.

CET1 ratio, non-performing-loan ratio, loan-loss coverage, and reported regulatory-capital headroom are optional quality inputs. Missing optional metrics do not block a calculation, but the missing metrics appear in warnings and quality details and cap model confidence at medium. SGX fundamentals use yFinance and are independently capped below high confidence; stale or materially incomplete inputs can reduce confidence to low.

Bank deposits, borrowings, and regulatory liquidity are operating/funding inputs rather than industrial-company financing adjustments. The bank model therefore does not calculate ordinary-company owner earnings, subtract working-capital movements, or subtract bank debt from residual-income equity value. It starts from common equity and after-interest common net income, so those ordinary-company adjustments would mix models and double count financing effects.

#### Illustrative response: SGX bank

A successful `SGX:D05` response uses the same common envelope as an ordinary-company valuation. This illustrative schema excerpt uses sample values; projected arrays are shortened and market-dependent values are not API constants:

```json
{
  "symbol": "SGX:D05",
  "exchange": "SGX",
  "currency": "SGD",
  "detected_company_type": "bank",
  "method": "bank_residual_income",
  "confidence": "medium",
  "intrinsic_value": {
    "bear": 10.25,
    "base": 12.4,
    "bull": 14.8
  },
  "model_details": {
    "method": "bank_residual_income",
    "normalized_roe": 0.12,
    "book_value_per_share": 11.1,
    "payout_ratio": 0.45,
    "usable_years": 5,
    "projected_book_equity": {
      "bear": [100.0, 105.0],
      "base": [100.0, 106.0],
      "bull": [100.0, 107.0]
    },
    "cet1_ratio": null,
    "npl_ratio": null,
    "loan_loss_coverage": null
  },
  "data_quality": {
    "primary_source": "yfinance_sgx",
    "stale": false,
    "missing_fields": []
  },
  "sources": {
    "common_equity": "yfinance",
    "net_income_common": "yfinance",
    "common_dividends": "yfinance",
    "diluted_shares": "yfinance",
    "current_price": "existing_quote_provider"
  }
}
```

#### REIT Distribution-And-NAV Method

The REIT engine values distributions and NAV per unit, not ordinary-company owner earnings:

```text
intrinsic value per unit = PV of ten annual DPU payments
                         + PV of terminal NAV per unit
```

Required inputs are at least three usable annual DPU observations, positive current units, current price, and a compatible reporting currency. NAV per unit is required for `reit_distribution_nav`; issuer-reported DPU/NAV is preferred, and missing facts never become zero. Starting DPU is the median of available independent components: latest trailing DPU, weighted three-year DPU (weights 1, 2, 3 from oldest to newest), and five-year median DPU. Base DPU growth is clamped to -3% through 3%; base NAV growth is clamped to -2% through 2.5%. The engine projects and discounts ten DPU payments, projects terminal NAV for ten years, applies the scenario terminal-NAV factor, and exposes the distribution and terminal present values separately.

| Scenario | Starting DPU | DPU growth | Required return | NAV growth | Terminal NAV factor |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Base minus 2 points, floor -5% | 10% | Base minus 1 point, clamped -2% through 2.5% | 85% |
| Base | 100% of normalized | Base, clamped -3% through 3% | 8.5% | Base, clamped -2% through 2.5% | 100% |
| Bull | 105% of normalized | Base plus 2 points, cap 5% | 7% | Base plus 1 point, clamped -2% through 2.5% | 110% |

When NAV is unavailable but DPU history is sufficient, `reit_distribution_only` instead adds a Gordon terminal distribution value, `DPU10 * (1 + terminal_growth) / (required_return - terminal_growth)`, discounted from year 10. Terminal distribution growth is 0%, 1.5%, and 2.5% for bear, base, and bull; required return must exceed terminal growth. This is an explicitly supported partial result with `low` confidence. Without sufficient DPU history, the result is `valuation_unreliable`, not an ordinary-company or bank fallback.

Optional quality facts are aggregate leverage, interest coverage, occupancy, weighted average lease expiry, recurring property capex, and material currency exposure. They appear in `model_details`, provenance, warnings, and quality details when available; missing optional facts can lower confidence without invalidating a complete DPU/NAV calculation.

#### Price Classification

The endpoint compares the independently refreshed current quote with base intrinsic value:

```text
margin_of_safety_price = base intrinsic value * 0.75
price_to_base_value = current price / base intrinsic value
upside_downside_percent = (base intrinsic value - current price) / current price * 100
```

Labels use this precedence:

1. `very_expensive` when current price is above bull intrinsic value.
2. `expensive` when current price is above 110% of base intrinsic value.
3. `cheap` when current price is at or below the 25% margin-of-safety price.
4. `fair` otherwise.

These labels describe the model comparison, not a buy or sell recommendation.

#### Response, Provenance, And Freshness

A reliable response contains:

- Public `symbol`, normalized `exchange`, and statement/price `currency`.
- `detected_company_type`, `method`, `classification_sources`, `status`, and `confidence`.
- `current_price` and a separate `price_as_of` timestamp.
- `intrinsic_value` with bear, base, bull, margin-of-safety price, price-to-base ratio, and upside/downside percentage.
- Model-specific `model_details`: owner-earnings normalization/history for ordinary companies; normalized ROE, book value per share, payout, projected book equity, and optional quality ratios for banks; or normalized DPU, NAV, distribution yield, optional REIT quality facts, and distribution-versus-terminal present values for REITs.
- Fixed model `assumptions`, `quality`, field-level `sources`, and `warnings`.
- `data_quality.primary_source`, `financials_as_of`, `valuation_as_of`, `next_refresh_at`, `stale`, and `missing_fields`.

#### Illustrative response: ordinary U.S. company

This illustrative response shape uses sample values; annual history and the source map are shortened for readability:

```json
{
  "symbol": "NASDAQ:AAPL",
  "exchange": "NASDAQ",
  "currency": "USD",
  "detected_company_type": "operating_company",
  "method": "owner_earnings_dcf",
  "classification_sources": ["provider_security_type", "statement_structure"],
  "status": "cheap",
  "confidence": "high",
  "current_price": 90.0,
  "price_as_of": "2026-07-10T10:15:00Z",
  "intrinsic_value": {
    "bear": 95.0,
    "base": 120.0,
    "bull": 150.0,
    "margin_of_safety_price": 90.0,
    "price_to_base_value": 0.75,
    "upside_downside_percent": 33.33
  },
  "model_details": {
    "method": "owner_earnings_dcf",
    "normalized_owner_earnings": 5500000000.0,
    "owner_earnings_per_share": 5.5,
    "maintenance_capex_method": "total_capital_expenditure",
    "annual_history": [
      {
        "period_end": "2025-12-31",
        "currency": "USD",
        "operating_cash_flow": 8000000000.0,
        "maintenance_capex": 1500000000.0,
        "maintenance_capex_method": "total_capital_expenditure",
        "stock_based_compensation": 1000000000.0,
        "interest_paid_outside_operating": 0.0,
        "owner_earnings": 5500000000.0
      }
    ],
    "derived_growth": 0.04,
    "usable_years": 5
  },
  "quality": {
    "eligible": true,
    "reasons": [],
    "details": {"usable_years": 5}
  },
  "assumptions": {
    "projection_years": 10,
    "margin_of_safety": 0.25,
    "scenarios": {
      "bear": {
        "starting_factor": 0.9,
        "initial_growth": 0.0,
        "required_return": 0.12,
        "terminal_growth": 0.02,
        "projection_years": 10
      },
      "base": {
        "starting_factor": 1.0,
        "initial_growth": 0.04,
        "required_return": 0.1,
        "terminal_growth": 0.025,
        "projection_years": 10
      },
      "bull": {
        "starting_factor": 1.05,
        "initial_growth": 0.07,
        "required_return": 0.08,
        "terminal_growth": 0.03,
        "projection_years": 10
      }
    }
  },
  "data_quality": {
    "primary_source": "sec_companyfacts",
    "financials_as_of": "2026-03-31",
    "valuation_as_of": "2026-07-10T10:15:00Z",
    "next_refresh_at": "2026-07-11T10:15:00Z",
    "stale": false,
    "missing_fields": []
  },
  "sources": {
    "operating_cash_flow": "sec_companyfacts",
    "capital_expenditure": "sec_companyfacts",
    "stock_based_compensation": "sec_companyfacts",
    "diluted_shares": "sec_companyfacts",
    "current_price": "existing_quote_provider"
  },
  "warnings": []
}
```

#### Illustrative response: ordinary SGX company

This illustrative `SGX:S63` response demonstrates the public symbol and SGD contract; values are examples only:

```json
{
  "symbol": "SGX:S63",
  "exchange": "SGX",
  "currency": "SGD",
  "detected_company_type": "operating_company",
  "method": "owner_earnings_dcf",
  "status": "fair",
  "confidence": "medium",
  "current_price": 7.15,
  "intrinsic_value": {
    "bear": 5.8,
    "base": 7.5,
    "bull": 9.1,
    "margin_of_safety_price": 5.625,
    "price_to_base_value": 0.9533,
    "upside_downside_percent": 4.9
  },
  "data_quality": {
    "primary_source": "yfinance_sgx",
    "stale": false,
    "missing_fields": []
  },
  "sources": {
    "operating_cash_flow": "yfinance",
    "current_price": "existing_quote_provider"
  }
}
```

#### Illustrative response: SGX REIT

This illustrative `SGX:C38U` response shows the complete DPU-and-NAV method; values are examples only:

```json
{
  "symbol": "SGX:C38U",
  "exchange": "SGX",
  "currency": "SGD",
  "detected_company_type": "reit",
  "method": "reit_distribution_nav",
  "status": "cheap",
  "confidence": "medium",
  "current_price": 1.85,
  "intrinsic_value": {
    "bear": 1.9,
    "base": 2.2,
    "bull": 2.6,
    "margin_of_safety_price": 1.65,
    "price_to_base_value": 0.8409,
    "upside_downside_percent": 18.9
  },
  "model_details": {
    "method": "reit_distribution_nav",
    "normalized_dpu": 0.105,
    "nav_per_unit": 2.08,
    "price_to_nav": 0.8894,
    "distribution_yield": 0.0568,
    "usable_years": 5,
    "present_value_distributions": {
      "bear": 0.62,
      "base": 0.82,
      "bull": 1.05
    },
    "present_value_terminal": {
      "bear": 1.28,
      "base": 1.38,
      "bull": 1.55
    }
  },
  "data_quality": {
    "primary_source": "yfinance_sgx",
    "stale": false,
    "missing_fields": []
  },
  "sources": {
    "distribution_per_unit": "yfinance",
    "nav_per_unit": "yfinance",
    "current_price": "existing_quote_provider"
  }
}
```

`sources` records the provider selected for each normalized field, not just a response-wide source label. U.S. fundamentals prefer SEC Company Facts when `STOCK_API_SEC_USER_AGENT` is configured. Missing compatible SEC facts may be filled at the fundamentals facade from a same-currency, same-period yFinance fact while retaining its field-level source. If the SEC user agent is not configured, U.S. requests use yFinance fundamentals, include an explicit fallback warning, and cannot receive high confidence. SGX uses yFinance fundamentals and is capped at medium confidence. `high` requires official primary facts, five usable years, compatible inputs, and no material model conflict; `medium` covers complete three-year results and consistent SGX/yFinance inputs; `low` covers stale inputs, material optional-data gaps, and the supported REIT distribution-only fallback. Missing required inputs always return `valuation_unreliable` rather than an invented low-confidence value.

Fundamentals and intrinsic scenarios use the daily valuation cache (`86400` seconds by default). Quotes use a separate five-minute cache (`300` seconds by default), so price-dependent ratios and labels can change without recalculating intrinsic value. A changed normalized-fundamentals timestamp or model version invalidates the model result.

When fundamentals refresh fails, an otherwise usable cache entry can be served for the stale window (`604800` seconds by default). The response then sets `data_quality.stale` to `true`, adds a warning, and reports low confidence. If there is no usable stale entry, required provider failure returns `502`. A `404` is returned only when an upstream provider conclusively identifies the symbol as not found, such as a typed SEC or quote not-found response. yFinance fundamentals failures return `502`, including unresolved symbols that yFinance does not distinguish from other upstream failures. Provider retry metadata such as `Retry-After` is preserved when available.

yFinance is an unofficial, provider-dependent source and may be incomplete, delayed, rate-limited, or unavailable. It is not an exchange-authoritative SGX feed. A production deployment that requires guaranteed data rights or service levels should implement an appropriately licensed source behind the existing provider interface; valuation math does not depend on a provider-specific payload.

#### Separation From Technical Analysis

`/valuation` is fundamentals-based and does not consume TradingView MCP indicators, scores, sentiment, trade setups, or multi-timeframe signals. `/technical` remains a TradingView MCP endpoint with its own provider cache and does not consume intrinsic values. `/analysis` remains separate yFinance OHLCV/P/E analysis, while `/quote` supplies the independently refreshed current price. The legacy `POST /api/v1/stocks/{symbol}/valuation` remains retired at `501` and is not an alias for this GET route.

### TradingView MCP Technical Analysis

```http
GET /api/v1/markets/{exchange}/{symbol}/technical?timeframe=1D&include_multi_timeframe=false
```

The technical endpoint returns TradingView MCP single-symbol technical analysis. It can include the provider's indicator objects, market sentiment, `stock_score` when available, and trade setup fields when available. A second cached TradingView scanner lookup supplies trailing P/E and 52-week high/low; `/technical` does not use yFinance.

Query parameters:

| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | Analysis timeframe |
| `include_multi_timeframe` | no | `false` | Add fixed 1W, 1D, 4h, 1h, and 15m alignment analysis |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/markets/NASDAQ/TSLA/technical?timeframe=1D&include_multi_timeframe=true"
```

Example response shape:

```json
{
  "symbol": "NASDAQ:TSLA",
  "exchange": "NASDAQ",
  "timeframe": "1D",
  "timestamp": "real-time",
  "source": "tradingview_mcp",
  "price_data": {
    "current_price": 428.11,
    "fifty_two_week_high": 555.45,
    "fifty_two_week_low": 349.2
  },
  "valuation_metrics": {
    "trailing_pe": 65.2,
    "primary_pe": "trailing"
  },
  "market_sentiment": {
    "overall_rating": 0,
    "buy_sell_signal": "NEUTRAL"
  },
  "rsi": {},
  "macd": {},
  "sma": {},
  "ema": {},
  "bollinger_bands": {},
  "atr": {},
  "volume_analysis": {},
  "support_resistance": {},
  "stock_score": 72,
  "grade": "B",
  "trend_state": "bullish",
  "trade_setup": {
    "risk_reward": 2.4
  },
  "warnings": [],
  "multi_timeframe": {
    "analysis_type": "Multi-Timeframe Alignment",
    "timeframes": {
      "1W": {"bias": "Bullish"},
      "1D": {"bias": "Bullish"},
      "4h": {"bias": "Bullish"},
      "1h": {"bias": "Neutral"},
      "15m": {"bias": "Bullish"}
    },
    "alignment": {
      "status": "MOSTLY BULLISH",
      "confidence": "High"
    },
    "recommendation": {
      "action": "BUY"
    }
  }
}
```

Client applications should treat indicator objects as provider-shaped dictionaries and read only the fields they need. `valuation_metrics.trailing_pe` is TradingView's trailing-twelve-month P/E; non-positive, non-finite, or unavailable values are returned as `null`. The 52-week fields are also nullable. Reference lookup failure does not fail the primary analysis and instead adds a warning.

`include_multi_timeframe=true` performs a fixed 1W → 1D → 4h → 1h → 15m alignment analysis. It is independent of the primary `timeframe` parameter. Partial or complete multi-timeframe failure preserves the primary response, keeps available timeframe results and errors under `multi_timeframe`, and adds a warning.

TradingView's internal bulk scanner can reduce upstream calls for candidate lists, but its raw rows do not match this advanced response: they omit the stock score, trade setup and quality, ATR augmentation, normalized reference fields, and multi-timeframe processing. No bulk `/technical` endpoint is exposed.

## Screeners

Screeners are market-wide endpoints. Results depend on the market universe available from TradingView MCP.

### Gainers

```http
GET /api/v1/markets/{exchange}/gainers?timeframe=1D&limit=25
```

Parameters:

| Name | Required | Default | Rules |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | See supported timeframe values |
| `limit` | no | `25` | `1-50` |

### Losers

```http
GET /api/v1/markets/{exchange}/losers?timeframe=1D&limit=25
```

Parameters are the same as gainers.

### Bollinger Scan

```http
GET /api/v1/markets/{exchange}/bollinger-scan?timeframe=1D&bbw_threshold=0.04&limit=50
```

Parameters:

| Name | Required | Default | Rules |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | See supported timeframe values |
| `bbw_threshold` | no | `0.04` | Must be greater than `0` |
| `limit` | no | `50` | `1-50` |

### Rating Filter

```http
GET /api/v1/markets/{exchange}/rating-filter?timeframe=1D&rating=2&limit=25
```

Parameters:

| Name | Required | Default | Rules |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | See supported timeframe values |
| `rating` | no | `2` | `-3` to `3` |
| `limit` | no | `25` | `1-50` |

Example screener response:

```json
[
  {
    "symbol": "NASDAQ:TSLA",
    "changePercent": 3.21,
    "indicators": {
      "close": 428.11,
      "RSI": 61.2
    }
  }
]
```

## Backtests

Backtests use Yahoo-backed OHLCV data through the TradingView MCP backtest service.

### Run One Strategy

```http
POST /api/v1/backtests/{exchange}/{symbol}
```

Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/backtests/SGX/D05" \
  -H "Content-Type: application/json" \
  -d "{\"strategy\":\"rsi\",\"period\":\"1y\",\"interval\":\"1d\"}"
```

Request body:

```json
{
  "strategy": "rsi",
  "period": "1y",
  "initial_capital": 10000,
  "commission_pct": 0.1,
  "slippage_pct": 0.05,
  "interval": "1d",
  "include_trade_log": false,
  "include_equity_curve": false
}
```

Example response shape:

```json
{
  "symbol": "D05.SI",
  "strategy": "rsi",
  "strategy_label": "RSI Oversold/Overbought",
  "period": "1y",
  "interval": "1d",
  "timeframe": "Daily (1d)",
  "candles_analyzed": 254,
  "date_from": "2025-07-09",
  "date_to": "2026-07-09",
  "initial_capital": 10000,
  "total_return_pct": 3.25,
  "win_rate_pct": 55.56,
  "total_trades": 9,
  "buy_and_hold_return_pct": 11.2,
  "vs_buy_and_hold_pct": -7.95,
  "recent_trades": [],
  "data_source": "Yahoo Finance",
  "disclaimer": "Past performance does not guarantee future results. For educational use only.",
  "timestamp": "2026-07-09T00:00:00+00:00"
}
```

### Compare Strategies

```http
POST /api/v1/backtests/{exchange}/{symbol}/compare
```

Example body:

```json
{
  "period": "2y",
  "initial_capital": 10000,
  "commission_pct": 0.1,
  "slippage_pct": 0.05,
  "interval": "1d"
}
```

Example response fields:

```json
{
  "symbol": "TSLA",
  "period": "2y",
  "interval": "1d",
  "winner": "macd",
  "ranking": [
    {
      "rank": 1,
      "strategy": "macd",
      "total_return_pct": 14.2,
      "win_rate_pct": 52.0,
      "total_trades": 25
    }
  ],
  "warnings": null,
  "disclaimer": "Past performance does not guarantee future results."
}
```

### Walk-Forward Backtest

```http
POST /api/v1/backtests/{exchange}/{symbol}/walk-forward
```

Example body:

```json
{
  "strategy": "macd",
  "period": "2y",
  "initial_capital": 10000,
  "commission_pct": 0.1,
  "slippage_pct": 0.05,
  "n_splits": 3,
  "train_ratio": 0.7,
  "interval": "1d"
}
```

Rules:

| Field | Default | Rules |
| --- | --- | --- |
| `n_splits` | `3` | `2-10` |
| `train_ratio` | `0.7` | Greater than `0`, less than `1` in API validation; provider expects a practical `0.5-0.9` range |

## Sentiment And News

By default, sentiment and news use free Reddit/RSS-backed services. If `MARKETAUX_API_TOKEN` is configured on the server, Marketaux-backed data is used.

### Sentiment

```http
GET /api/v1/sentiment/{symbol}?category=all&limit=20
```

Parameters:

| Name | Required | Default | Rules |
| --- | --- | --- | --- |
| `symbol` | yes | none | Symbol to analyze |
| `category` | no | `all` | Provider category |
| `limit` | no | `20` | `1-100` |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/sentiment/TSLA?category=stocks&limit=20"
```

### News

```http
GET /api/v1/news?symbol=TSLA&category=stocks&limit=10
```

Parameters:

| Name | Required | Default | Rules |
| --- | --- | --- | --- |
| `symbol` | no | none | Optional symbol filter |
| `category` | no | `stocks` | Provider category |
| `limit` | no | `10` | `1-100` |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/news?symbol=TSLA&category=stocks&limit=10"
```

## Legacy Stock Routes

These routes are kept for compatibility with older clients. New applications should use `/api/v1/markets/...`.

| Method | Endpoint | Replacement |
| --- | --- | --- |
| `GET` | `/api/v1/stocks/{symbol}/quote?exchange=NASDAQ` | `/api/v1/markets/{exchange}/{symbol}/quote` |
| `GET` | `/api/v1/stocks/{symbol}/technicals?exchange=NASDAQ&timeframe=1D&include_multi_timeframe=false` | `/api/v1/markets/{exchange}/{symbol}/technical` |
| `POST` | `/api/v1/stocks/{symbol}/valuation?exchange=NASDAQ&timeframe=1D` | Not supported; use the canonical market GET for eligible ordinary companies or banks |
| `GET` | `/api/v1/stocks/{symbol}/fundamentals` | Not supported |

`/api/v1/stocks/{symbol}/fundamentals` and the legacy stock valuation POST return HTTP `501` because the previous yFinance fundamentals and DCF model were removed. The canonical market valuation GET is a separate typed, model-routed implementation and does not revive or alias the legacy request contract.

## Error Handling

Common responses:

| HTTP Status | Meaning |
| --- | --- |
| `200` | Request succeeded; valuation may intentionally report `valuation_unreliable` without numerical claims |
| `404` | An upstream provider conclusively identified the symbol or data as not found |
| `422` | Request validation failed |
| `500` | Missing server dependency or configuration issue |
| `502` | Upstream provider error, including unresolved yFinance fundamentals failures that are not conclusively distinguishable from other failures |
| `503` | Retryable upstream provider error |

Example conclusive not-found error:

```json
{
  "detail": "No data found for MISSING on NASDAQ."
}
```

Client recommendations:

- Treat `404` as a conclusive symbol/data not-found result from an upstream provider.
- Treat `422` as a request-shape bug in the client.
- Retry `503` with backoff.
- Treat `502` as a temporary provider error. It can also represent an unresolved symbol when yFinance fundamentals retrieval does not distinguish not-found from upstream failure.
- Treat `200` plus `status: "valuation_unreliable"` as a supported refusal, and show `quality.reasons` rather than inventing a value.
- Market data is provider-dependent and may change during trading hours.

## JavaScript Client Example

```javascript
const API_BASE_URL = "http://127.0.0.1:8000";

async function apiGet(path) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `API request failed: ${response.status}`);
  }

  return response.json();
}

async function apiPost(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `API request failed: ${response.status}`);
  }

  return response.json();
}

export async function getTechnicalAnalysis(
  exchange,
  symbol,
  timeframe = "1D",
  includeMultiTimeframe = false
) {
  return apiGet(
    `/api/v1/markets/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}/technical?timeframe=${encodeURIComponent(timeframe)}&include_multi_timeframe=${includeMultiTimeframe}`
  );
}

export async function getIntrinsicValuation(exchange, symbol) {
  return apiGet(
    `/api/v1/markets/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}/valuation`
  );
}

export async function runBacktest(exchange, symbol) {
  return apiPost(
    `/api/v1/backtests/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}`,
    {
      strategy: "rsi",
      period: "1y",
      interval: "1d",
      initial_capital: 10000,
      commission_pct: 0.1,
      slippage_pct: 0.05,
    }
  );
}
```

## Python Client Example

```python
import requests

API_BASE_URL = "http://127.0.0.1:8000"


def get_json(path: str):
    response = requests.get(f"{API_BASE_URL}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def post_json(path: str, body: dict):
    response = requests.post(f"{API_BASE_URL}{path}", json=body, timeout=30)
    response.raise_for_status()
    return response.json()


technical = get_json("/api/v1/markets/SGX/D05/technical?timeframe=1D")
valuation = get_json("/api/v1/markets/SGX/S63/valuation")
backtest = post_json(
    "/api/v1/backtests/TVC/XAUUSD",
    {"strategy": "rsi", "period": "1y", "interval": "1d"},
)

print(technical["market_sentiment"]["buy_sell_signal"])
print(valuation["method"], valuation["status"])
print(backtest["symbol"], backtest["candles_analyzed"])
```

## Server Configuration

Environment variables:

```powershell
$env:STOCK_API_DEFAULT_EXCHANGE = "NASDAQ"
$env:STOCK_API_DEFAULT_TIMEFRAME = "1D"
```

Valuation settings (defaults shown except for the identifying SEC user agent):

```powershell
$env:STOCK_API_SEC_USER_AGENT = "stock-api your-email@example.com"
$env:STOCK_API_VALUATION_CACHE_TTL_SECONDS = "86400"
$env:STOCK_API_VALUATION_QUOTE_TTL_SECONDS = "300"
$env:STOCK_API_VALUATION_STALE_TTL_SECONDS = "604800"
```

SEC requests are made only when the user agent is present. Without it, U.S. valuation requests use yFinance fallback fundamentals and include a warning.

TradingView MCP tuning:

```powershell
$env:TRADINGVIEW_MCP_CACHE_TTL = "60"
$env:TRADINGVIEW_MCP_STALE_TTL = "21600"
$env:TRADINGVIEW_MCP_RETRY_DELAYS = "1.0,4.0"
$env:TRADINGVIEW_MCP_MAX_INFLIGHT = "2"
$env:TRADINGVIEW_MCP_SOCKET_TIMEOUT = "20"
```

Optional Marketaux:

```powershell
$env:MARKETAUX_API_TOKEN = "..."
```

## Notes For Integrators

- Prefer `/api/v1/markets/...` for all new applications.
- The legacy `/api/v1/stocks/...` endpoints may be removed in a future breaking version.
- Intrinsic valuation and price labels are model estimates, not investment advice.
- Ordinary-company, bank, and REIT intrinsic valuations are model-routed automatically; failed eligibility checks require explicit `valuation_unreliable` handling.
- Keep intrinsic valuation separate from TradingView technical signals in client decision logic.
- Backtest results are historical simulations and are not predictive.
- Market-wide screeners can be unavailable for markets where TradingView MCP does not provide a usable symbol universe.
- Always URL-encode `exchange` and `symbol` values in client applications.
