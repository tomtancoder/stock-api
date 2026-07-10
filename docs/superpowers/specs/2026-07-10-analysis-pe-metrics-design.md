# P/E Metrics in yFinance Analysis

## Goal

Add valuation metrics to `GET /api/v1/markets/{exchange}/{symbol}/analysis` so existing application consumers receive P/E data without making a second API request. Keep the TradingView-backed `/technical` endpoint unchanged.

## API Contract

The analysis response gains a top-level `valuation_metrics` object:

```json
{
  "valuation_metrics": {
    "trailing_pe": 22.84,
    "forward_pe": 19.86,
    "diluted_eps_ttm": 16.83,
    "forward_eps": 19.36,
    "primary_pe": "trailing",
    "pe_calculated": false
  }
}
```

All numeric values are nullable. `primary_pe` is always `"trailing"`; forward P/E is never substituted into the trailing field. `pe_calculated` is true only when `trailing_pe` was derived by this API from price and trailing diluted EPS.

## Data Flow

The yFinance analysis service continues to fetch price history and lightweight `fast_info`. It also performs a best-effort metadata lookup for `trailingPE`, `forwardPE`, `trailingEps`, and `forwardEps`.

Trailing P/E is selected in this order:

1. Use a finite, positive `trailingPE` supplied by yFinance.
2. Otherwise calculate `current_price / diluted_eps_ttm` when both values are finite and positive.
3. Otherwise return null.

The trailing EPS source is yFinance `trailingEps`, with `DilutedEPS` from `get_income_stmt(freq="trailing")` as a fallback. Forward P/E uses a finite, positive yFinance `forwardPE`; if it is absent, the API calculates it from current price and finite, positive `forwardEps`. Forward values remain explicitly forward-looking and never become the primary P/E.

Metadata responses are cached with the existing `STOCK_API_CACHE_TTL_SECONDS` setting, which defaults to one hour. Price history and technical-indicator behavior are not changed by this feature.

## Error Handling

Fundamental metadata is optional. A timeout, rate limit, parser failure, missing field, zero EPS, or negative EPS must not fail `/analysis`. The endpoint returns the existing price and indicator data with nullable valuation fields.

The API accepts only finite, positive P/E and EPS inputs. It does not convert negative values to absolute values and does not impose an arbitrary maximum P/E cap.

## Testing

Unit tests cover:

- direct trailing and forward P/E values;
- calculated trailing P/E when the direct ratio is missing;
- trailing-statement diluted EPS fallback;
- rejection of zero, negative, NaN, and infinite inputs;
- metadata lookup failure returning null valuation fields without failing analysis;
- the public `/analysis` response retaining the new object.

The focused yFinance tests and the full mocked pytest suite must pass. `git diff --check` must report no whitespace errors.

## Scope

This change modifies only the yFinance-backed analysis response and its tests/documentation. It does not add a new fundamentals endpoint, change `/quote`, change `/technical`, or modify application-side extraction logic.
