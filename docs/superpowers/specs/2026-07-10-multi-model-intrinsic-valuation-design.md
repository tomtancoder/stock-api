# Multi-Model Intrinsic Valuation

## Goal

Add a transparent intrinsic-valuation endpoint that estimates whether a listed security is cheap, fairly valued, or expensive without presenting a single estimate as certain. Support ordinary operating companies, banks, and REITs through separate valuation models, with first-class handling for US and SGX symbols.

The endpoint must return bear, base, and bull values, a margin-of-safety price, model confidence, freshness, input provenance, and warnings. It must refuse to calculate when the selected model or available data cannot support a defensible value.

## Public API

Add the canonical endpoint:

```http
GET /api/v1/markets/{exchange}/{symbol}/valuation
```

Examples:

```text
GET /api/v1/markets/NASDAQ/AAPL/valuation
GET /api/v1/markets/SGX/S63/valuation
GET /api/v1/markets/SGX/D05/valuation
```

Version 1 selects assumptions and the valuation model automatically. Clients cannot force a model. Bear, base, and bull scenarios provide the supported sensitivity analysis without allowing a caller to make an incompatible model appear authoritative.

The following routes remain independent:

- `GET /api/v1/markets/{exchange}/{symbol}/technical` continues to return TradingView MCP technical analysis.
- `GET /api/v1/markets/{exchange}/{symbol}/analysis` continues to return yFinance-backed price analysis and P/E metadata.
- The legacy `POST /api/v1/stocks/{symbol}/valuation` continues to return `501`; it is not revived as an alias.

## Supported Company Types

The model router selects exactly one method:

| Detected type | Method |
| --- | --- |
| Ordinary operating company | `owner_earnings_dcf` |
| Bank | `bank_residual_income` |
| REIT or property trust | `reit_distribution_nav` |
| Unsupported or ambiguous | `valuation_unreliable` |

Insurers, ETFs, mutual funds, commodities, cryptocurrencies, pre-revenue companies, and other structures without an approved model return `valuation_unreliable` in version 1.

Classification uses, in order:

1. Explicit security or quote type.
2. Sector and industry metadata.
3. Issuer or filing classification.
4. Financial-statement structure as corroborating evidence.
5. Issuer-name patterns only as supporting evidence.

REIT classification takes precedence over a broad financial or real-estate sector label. A bank classification requires a bank industry classification plus compatible bank financial statements. Conflicting evidence returns `valuation_unreliable`; a failed bank or REIT calculation never falls back to the ordinary-company model.

Every response reports `detected_company_type`, `method`, `classification_sources`, and `confidence`.

## Architecture

Keep provider access and deterministic calculations separate:

1. `market_symbols` owns public and provider symbol normalization.
2. `valuation_fundamentals` retrieves, normalizes, caches, and records the provenance of financial facts.
3. `valuation_router` classifies the company and dispatches to one model.
4. `owner_earnings_valuation` calculates ordinary-company values.
5. `bank_valuation` calculates bank values.
6. `reit_valuation` calculates REIT values.
7. `valuation_service` combines the selected model result with a current quote, freshness metadata, classification, and warnings.
8. The market route validates the path, calls the service, and maps typed service failures to HTTP responses.

Pure model functions accept normalized Pydantic data and do not perform network access. Provider implementations return normalized facts with field-level sources rather than provider-shaped dictionaries.

## Symbol and Currency Handling

Use one shared resolver for quote, analysis, backtest, and valuation behavior.

For SGX:

- Accept both `D05` and `D05.SI`.
- Normalize provider access to `D05.SI`.
- Return the public identifier `SGX:D05`.
- Preserve `SGD` for price and per-share values.

The API must not combine financial statements, price, and share counts expressed in incompatible currencies or units. A currency or unit mismatch returns `valuation_unreliable` with the mismatched fields. No missing financial field silently becomes zero.

## Data Sources and Provenance

### US securities

Use SEC EDGAR as the primary source for financial statements and filing history:

- Operating cash flow
- Capital expenditure
- Stock-based compensation
- Interest paid and its cash-flow classification
- Revenue and net income
- Common equity and diluted shares
- Cash and debt
- Filing dates and periods

Use SEC submissions to map tickers to CIK identifiers and detect new filings. Normalize alternative standard XBRL concepts for the same economic fact and retain the selected concept, filing accession, form, period, unit, and filed date in internal provenance.

Use yFinance only as a fallback for missing financial facts and metadata. Reuse the existing quote provider for current price, currency, and quote timestamp.

### SGX and other non-US securities

Use yFinance annual, quarterly, and trailing income statements, cash-flow statements, balance sheets, share history, dividends, and metadata. Normalize SGX symbols to `.SI` before provider access. Support row aliases, sign differences, and IFRS interest classifications for the same economic fact.

SGXNET is the authoritative issuer-announcement channel, but its structured real-time XML feed is a separate data service. Keep the provider interface open for a licensed SGX data source without coupling it to any valuation model.

An SGX valuation based only on yFinance cannot receive high confidence. Missing optional facts lower confidence; missing required facts produce `valuation_unreliable` or the explicitly documented lower-confidence fallback for that model.

### Response provenance

Return field-level provenance:

```json
{
  "sources": {
    "operating_cash_flow": "sec_companyfacts",
    "capital_expenditure": "sec_companyfacts",
    "diluted_shares": "sec_companyfacts",
    "current_price": "existing_quote_provider"
  }
}
```

The response must also include `primary_source`, `financials_as_of`, `valuation_as_of`, `price_as_of`, `next_refresh_at`, `stale`, and `missing_fields`.

## Ordinary Operating Companies

### Owner earnings

For each usable annual or trailing period:

```text
owner earnings = operating cash flow
               - maintenance capex
               - stock-based compensation
               - interest paid classified outside operating cash flow
```

Operating cash flow already contains working-capital movements, so the model does not subtract working capital again. Under US GAAP, interest paid is normally already included in operating cash flow and the final adjustment is zero. For an IFRS or SGX statement that classifies interest paid as financing, subtract the financing-classified interest once so owner earnings remain comparable and after interest. If the cash-flow classification cannot be reconciled, lower confidence or return `valuation_unreliable` when the amount is material.

Maintenance-capex selection order:

1. Use issuer-reported maintenance capex when the fact is explicitly identified.
2. Otherwise use total capital expenditure as the conservative automatic default.
3. Report `maintenance_capex_method` and the source values.

Stock-based compensation is treated as an owner expense. Capex cash outflows are normalized to positive deduction amounts before the formula is applied.

### Normalized starting earnings

Require at least three usable fiscal years and prefer five. Select the median of:

1. Latest trailing-twelve-month owner earnings.
2. Weighted three-year average owner earnings, with the most recent year receiving the highest weight.
3. Five-year median owner-earnings margin multiplied by current trailing revenue.

If a component is unavailable, calculate the median from the remaining components only when at least two independent components remain. Otherwise return `valuation_unreliable`.

### Growth

Derive base growth from per-share revenue and owner-earnings history. Reject non-finite values and clamp base growth between -15% and 12%. Growth fades annually toward the scenario terminal rate over ten projection years.

| Scenario | Starting earnings | Initial growth | Required return | Terminal growth |
| --- | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Base minus 4 percentage points, minimum -20% | 12% | 2% |
| Base | 100% of normalized | Derived growth, between -15% and 12% | 10% | 2.5% |
| Bull | 105% of normalized | Base plus 3 percentage points, maximum 15% | 8% | 3% |

Project and discount each annual owner-earnings amount. Calculate terminal value as `OE10 * (1 + terminal_growth) / (required_return - terminal_growth)` and discount it from year 10. Required return must be greater than terminal growth.

After the interest-classification normalization, owner earnings are after interest expense and are discounted directly to an equity value. The model does not label this value as enterprise value or subtract debt a second time. Leverage affects eligibility and confidence. Version 1 does not add an automatic excess-cash adjustment.

Divide equity value by current diluted shares to obtain per-share intrinsic value.

## Banks

Use a residual-income model:

```text
excess return in year t = (ROE in year t - required return) * beginning common equity
intrinsic equity = current common equity + present value of future excess returns
```

Require positive common shareholders' equity and at least three usable years of net income attributable to common shareholders, diluted shares, dividends, and ROE. Prefer five years.

Normalize ROE and dividend payout from available history. Project common book equity through retained earnings using `ending_equity = beginning_equity + net_income - common_dividends`, where projected net income is beginning equity multiplied by projected ROE. Reject payout ratios outside 0% to 100% rather than silently clamping them. Forecast ten years and fade ROE toward the required return so excess returns converge to zero by year 10. Version 1 assigns no persistent terminal excess return after year 10.

Use required returns of 12%, 10%, and 8% for bear, base, and bull scenarios. Bear and bull normalized ROE assumptions use 90% and 105% of base normalized ROE, respectively, before the fade begins. Divide intrinsic common equity by current diluted shares.

Optional bank-quality facts include CET1 ratio, non-performing-loan ratio, loan-loss coverage, and reported regulatory capital headroom. Missing optional metrics lower confidence but do not block a calculation. Negative common equity, fewer than three usable years, or irreconcilable equity and earnings units produce `valuation_unreliable`.

## REITs and Property Trusts

Use a distribution-and-NAV model:

```text
intrinsic value per unit = present value of 10 years of DPU
                         + present value of terminal NAV per unit
```

Required inputs are distribution per unit or distributable income, units outstanding, NAV per unit, current price, and reporting currency. Prefer issuer-reported DPU and distributable income. FFO and AFFO are supporting facts because AFFO is not standardized consistently across issuers.

Normalize starting DPU by taking the median of:

1. Latest trailing DPU.
2. Weighted three-year DPU.
3. Five-year median DPU.

Require at least three usable distribution years. Derive base DPU growth from per-unit history and clamp it between -3% and 3%. Derive base NAV growth from per-unit history and clamp it between -2% and 2.5%; bear and bull NAV growth move one percentage point below and above base within the same bounds.

| Scenario | Starting DPU | DPU growth | Required return | Terminal NAV |
| --- | ---: | ---: | ---: | ---: |
| Bear | 90% of normalized | Base minus 2 percentage points, minimum -5% | 10% | 85% of projected NAV |
| Base | 100% of normalized | Derived growth, between -3% and 3% | 8.5% | 100% of projected NAV |
| Bull | 105% of normalized | Base plus 2 percentage points, maximum 5% | 7% | 110% of projected NAV |

Return the present value contributed by distributions and terminal NAV separately. Supporting quality facts include aggregate leverage, interest coverage, occupancy, weighted average lease expiry, recurring property capex, and material currency exposure.

If NAV is missing but at least three years of reliable DPU exist, use a Gordon-growth terminal value and return a lower-confidence `reit_distribution_only` result. Use terminal distribution growth of 0%, 1.5%, and 2.5% for bear, base, and bull, respectively, and require the scenario return to exceed terminal growth. If DPU history is also insufficient, return `valuation_unreliable`.

## Price Classification

Calculate:

```text
margin_of_safety_price = base intrinsic value * 0.75
price_to_base_value = current price / base intrinsic value
upside_downside_percent = (base intrinsic value - current price) / current price * 100
```

Apply labels in this precedence order:

1. `very_expensive` when current price is above the bull intrinsic value.
2. `expensive` when current price is above 110% of base intrinsic value.
3. `cheap` when current price is at or below 75% of base intrinsic value.
4. `fair` otherwise.

All three intrinsic values must be finite and positive, with `bear <= base <= bull`, before price classification. If the model result is unreliable, omit numerical classification fields that cannot be supported and return `valuation_unreliable` with reasons.

## Response Contract

Use a common envelope with model-specific details:

```json
{
  "symbol": "SGX:S63",
  "exchange": "SGX",
  "currency": "SGD",
  "detected_company_type": "operating_company",
  "method": "owner_earnings_dcf",
  "classification_sources": ["provider_industry", "statement_structure"],
  "status": "fair",
  "confidence": "medium",
  "current_price": 7.15,
  "price_as_of": "2026-07-10T10:15:00Z",
  "intrinsic_value": {
    "bear": 5.80,
    "base": 7.50,
    "bull": 9.10,
    "margin_of_safety_price": 5.63,
    "price_to_base_value": 0.9533,
    "upside_downside_percent": 4.9
  },
  "model_details": {},
  "quality": {
    "eligible": true,
    "reasons": []
  },
  "assumptions": {
    "projection_years": 10,
    "margin_of_safety": 0.25,
    "scenarios": {}
  },
  "data_quality": {
    "primary_source": "yfinance_sgx",
    "financials_as_of": "2025-12-31",
    "valuation_as_of": "2026-07-10T00:00:00Z",
    "next_refresh_at": "2026-07-11T00:00:00Z",
    "stale": false,
    "missing_fields": []
  },
  "sources": {},
  "warnings": []
}
```

`model_details` is a discriminated union keyed by `method`:

- Owner-earnings history, normalized owner earnings, per-share amount, growth derivation, and maintenance-capex method.
- Bank normalized ROE, book value per share, payout ratio, projected book equity, and optional regulatory metrics.
- REIT normalized DPU, NAV per unit, price-to-NAV, distribution yield, leverage, and distribution-versus-terminal-value contributions.

All numeric values are nullable only where the contract explicitly permits a lower-confidence partial result. Never emit NaN or infinity.

## Refresh and Caching

Separate slow-changing valuation inputs from current price:

| Data | Refresh policy |
| --- | --- |
| SEC or SGX/yFinance filing check | Daily, plus immediate invalidation when a new filing is detected |
| Normalized fundamentals and intrinsic-value scenarios | Once daily |
| Current quote | Five minutes while the market is open; a longer closed-market cache is permitted |
| Cheap/fair/expensive status | Recalculate whenever the quote refreshes |
| Technical analysis | Independent existing provider cache |

Cache normalized financial inputs separately from the final quote comparison. A new filing, changed normalized facts, changed model version, or changed configured assumptions invalidates the intrinsic-value cache.

The intrinsic value must not fluctuate on every quote update. Only price-dependent fields and the classification are recomputed with a fresh quote.

## Error Handling

- Return `404` when the symbol cannot be resolved by any provider.
- Return `502` when required providers fail, no usable cached data exists, and the symbol is otherwise valid.
- Return `200` with `status: "valuation_unreliable"` for a valid but unsupported company type, ambiguous model selection, insufficient financial data, incompatible units, or an inapplicable model.
- Return a cached result with `data_quality.stale: true` and a warning when live refresh fails but a usable cached valuation exists.
- Missing optional data lowers confidence and appears in `missing_fields`.
- Missing required data never silently becomes zero and never triggers a fallback to an incompatible model.
- Preserve provider retry metadata such as `Retry-After` when no cached result can be served.

## Confidence

Confidence reflects data and model fitness, not predicted market performance.

- `high`: official primary financial facts, five usable years, complete required inputs, consistent units, stable normalized inputs, and no material model conflict.
- `medium`: at least three usable years with complete required inputs, or SGX/non-US yFinance fundamentals with consistent values.
- `low`: an explicitly supported partial result such as REIT distribution-only valuation, stale inputs, or material optional-data gaps.

An unsupported model or missing required data returns `valuation_unreliable`, not `low` confidence with an invented value.

## Testing

Normal tests must mock every network provider.

### Pure model tests

- Owner-earnings calculation, capex sign normalization, SBC deduction, IFRS financing-classified interest adjustment, and no working-capital double subtraction.
- Starting-value normalization with five, three, and insufficient histories.
- Growth caps, fade, terminal value, and discounting for each ordinary-company scenario.
- Bank excess-return calculation, book-equity projection, payout handling, and ROE fade.
- REIT DPU discounting, NAV projection, terminal NAV contribution, and distribution-only fallback.
- Classification thresholds, including exact 75% and 110% boundaries and `very_expensive` precedence.
- Bear value less than or equal to base value, and base value less than or equal to bull value.
- Rejection of missing, negative where prohibited, non-finite, incompatible-currency, and incompatible-unit inputs.

### Router and provider tests

- US ordinary-company classification with SEC facts.
- SGX ordinary-company classification and `.SI` normalization.
- SGX bank classification and residual-income dispatch.
- SGX REIT classification and distribution/NAV dispatch.
- Conflicting or ambiguous classification returning `valuation_unreliable`.
- Field aliases, reporting-period selection, amendments, duplicate facts, and restatements.
- Field-level provenance and confidence assignment.
- Fresh, stale, invalidated, and provider-failure cache behavior.

### API tests

- Canonical market valuation route and response-model validation.
- Both `SGX/D05` and `SGX/D05.SI` path forms.
- SGD output and currency mismatch rejection.
- `404`, `502`, stale success, and `valuation_unreliable` behavior.
- Model-specific response details.
- Freshness and source metadata.
- Regression coverage proving `/technical`, `/analysis`, `/quote`, and the legacy stock valuation route remain unchanged.

### Optional live tests

Mark live provider checks with `@pytest.mark.live` and keep them opt-in. Cover one US ordinary company and one SGX fixture for each supported model. Assert schema, source metadata, finite values, currency consistency, and scenario ordering rather than hard-coded market values.

Run the focused valuation tests, the full mocked suite, and `git diff --check` before completion.

## Documentation

Update `README.md` and `API_DOCUMENTATION.md` with:

- The canonical route and examples for US and SGX.
- Model routing and supported company types.
- Ordinary-company, bank, and REIT assumptions.
- Daily valuation versus quote refresh behavior.
- Confidence, provenance, and `valuation_unreliable` semantics.
- The separation from `/technical`.
- The yFinance usage caveat and the future licensed-provider boundary for production market data.

## Scope Boundaries

Version 1 does not:

- Produce buy or sell advice.
- Allow clients to force a valuation model.
- Value insurers, funds, commodities, cryptocurrencies, or pre-revenue companies.
- Scrape arbitrary annual-report PDFs.
- Integrate a licensed SGX feed without credentials and a separate provider decision.
- Merge technical signals into intrinsic valuation.
- Change existing technical, analysis, quote, backtest, sentiment, or news behavior.

## References

- Berkshire Hathaway annual reports on intrinsic value and owner economics: <https://www.berkshirehathaway.com/letters/letters.html>
- SEC EDGAR APIs: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- yFinance API and usage notice: <https://ranaroussi.github.io/yfinance/>
- SGX News and Corporate Actions data services: <https://www.sgx.com/data-connectivity/news-corporate-actions>
- Aswath Damodaran financial-service valuation models: <https://pages.stern.nyu.edu/~adamodar/New_Home_Page/eqspread.htm>
- Nareit FFO and AFFO definitions: <https://www.reit.com/glossary/funds-operation-ffo> and <https://www.reit.com/glossary/adjusted-funds-operations-affo>
