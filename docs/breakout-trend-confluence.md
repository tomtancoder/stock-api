# Breakout Trend Confluence API

Breakout Trend Confluence (BT) is a transparent daily breakout model. It scores six independent evidence families out of 18 points:

| Component | Maximum |
| --- | ---: |
| Breakout structure | 4 |
| Trend alignment | 4 |
| Volume participation | 3 |
| Momentum and trend strength | 3 |
| Relative strength and market regime | 2 |
| Entry quality and risk | 2 |

Daily bars determine the setup state, score, and rating. The optional 4H result is entry confirmation only and never changes the daily score. The model uses adjusted OHLCV and benchmark history; it has no dependency on valuation, intrinsic value, margin of safety, analyst targets, or DCF output.

## Endpoints

Single symbol, preserving exchange identity:

```http
GET /api/v1/markets/{exchange}/{symbol}/breakout-analysis
```

Query parameters:

- `benchmark`: optional Yahoo benchmark override; defaults to `SPY` for US symbols and `^STI` for SGX.
- `include_four_hour`: defaults to `false`.

Batch scan:

```http
GET /api/v1/screener/breakouts
```

```bash
curl "http://127.0.0.1:8000/api/v1/screener/breakouts?market=us&minimum_score=12&include_four_hour=true&limit=50"
```

The batch query supports `market=all|us|sg`, `minimum_score=0..18`, optional `rating`, optional `setup_state`, optional non-negative `maximum_extension_atr`, `include_four_hour`, and `limit=1..500`. Downloads are chunked, benchmarks are reused within a scan, and a failed ticker appears in `errors` without discarding successful results.

## Response example

```json
{
  "symbol": "ACME",
  "exchange": "NASDAQ",
  "benchmark_symbol": "SPY",
  "as_of": "2026-07-15T00:00:00",
  "data_status": "ready",
  "rating": "Strong Setup",
  "setup_state": "Breakout Retest",
  "total_score": 16,
  "four_hour_status": "4H Retest Held",
  "breakout": {"score": 4, "max_score": 4, "flags": ["BREAKOUT_RETEST_HELD"], "explanation": ["The prior resistance level was retested and held."]},
  "trend": {"score": 4, "max_score": 4, "flags": ["EMA_STACK_BULLISH"], "explanation": ["Price and EMA20, EMA50, and EMA200 are positively aligned."]},
  "volume": {"score": 2, "max_score": 3, "flags": ["VOLUME_CONFIRMED"], "explanation": ["Volume was 1.63 times its 50-session average."]},
  "momentum": {"score": 2, "max_score": 3, "flags": ["RSI_BULLISH"], "explanation": ["RSI14 was 62.4, within the preferred bullish range."]},
  "relative_strength": {"score": 2, "max_score": 2, "flags": ["RS_OUTPERFORMING", "MARKET_REGIME_POSITIVE"], "explanation": ["The stock outperformed its benchmark over 63 sessions."]},
  "entry_quality": {"score": 2, "max_score": 2, "flags": ["ENTRY_NOT_EXTENDED"], "explanation": ["Price is 0.39 ATR above the breakout level with 3.5% initial risk."]},
  "level": {"window": 55, "price": 100.0, "buffer": 0.47, "breakout_bars_ago": 5, "breakout_percent": 0.012, "close_location": 0.82, "base_depth_pct": 0.11},
  "indicators": {"close": 101.2, "ema20": 98.7, "ema50": 94.4, "ema200": 87.2, "ema200_prior": 86.5, "rsi14": 62.4, "atr14": 3.1, "adx14": 24.8, "plus_di14": 31.1, "minus_di14": 16.2, "cmf20": 0.09, "volume_ratio": 1.63, "stock_return_63": 0.18, "benchmark_return_63": 0.07},
  "risk": {"invalidation_price": 97.675, "extension_atr": 0.3871, "initial_risk_pct": 0.0348},
  "flags": ["BREAKOUT_RETEST_HELD", "EMA_STACK_BULLISH", "VOLUME_CONFIRMED", "RS_OUTPERFORMING", "ENTRY_NOT_EXTENDED"],
  "explanation": ["The prior resistance level was retested and held.", "The stock outperformed its benchmark over 63 sessions.", "The entry is not materially extended."],
  "warnings": []
}
```

## Data status and rating safety

`ready` and `partial` responses can carry a score. Partial results are capped at `Watchlist`. `insufficient_history`, `stale`, and `error` are not investment ratings: their `rating` and `total_score` are `null`. Clients must render these as an em dash and show the data status; never transform them into `Avoid 0/18`.

EMA200 requires 200 valid closes and the overall analysis requires at least 220 daily bars. Missing benchmark or volume inputs produce `partial`, not fabricated zero evidence. Rating caps are included in machine flags and plain-language explanations.

## Frontend migration from VZC

| Old VZC column | New field / column |
| --- | --- |
| `VZC Rating` | `rating` / BT Rating |
| `VZC` | `total_score` / BT Score, displayed as `n/18` |
| `Setup` | `setup_state` / Breakout State |
| `MOS` | `level.breakout_percent` / Breakout % |
| `Zone` | `level.price` / Pivot |
| `R/R` | `risk.initial_risk_pct` or `risk.extension_atr` |

Recommended additional columns are volume ratio, RSI/ADX, 4H status, and data status. Component scores belong in an expandable row or detail panel. Keep all valuation fields in the separate research experience.
