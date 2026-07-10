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
GET /api/v1/markets/SGX/D05/technical?timeframe=1D
POST /api/v1/backtests/SGX/D05
```

Internally, Singapore quote, analysis, and backtest data uses Yahoo-style `.SI` symbols such as `D05.SI`. TradingView MCP technical analysis strips that suffix and returns TradingView symbols such as `SGX:D05`.

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

### TradingView MCP Technical Analysis

```http
GET /api/v1/markets/{exchange}/{symbol}/technical?timeframe=1D
```

The technical endpoint returns TradingView MCP single-symbol technical analysis. It can include the provider's indicator objects, market sentiment, `stock_score` when available, and trade setup fields when available.

Query parameters:

| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `timeframe` | no | `1D` | Analysis timeframe |

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/markets/TVC/XAUUSD/technical?timeframe=1D"
```

Example response shape:

```json
{
  "symbol": "TVC:GOLD",
  "exchange": "TVC",
  "timeframe": "1D",
  "timestamp": "real-time",
  "source": "tradingview_mcp",
  "price_data": {
    "current_price": 4114.67
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
  }
}
```

Client applications should treat indicator objects as provider-shaped dictionaries and read only the fields they need.

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
| `GET` | `/api/v1/stocks/{symbol}/technicals?exchange=NASDAQ&timeframe=1D` | `/api/v1/markets/{exchange}/{symbol}/technical` |
| `POST` | `/api/v1/stocks/{symbol}/valuation?exchange=NASDAQ&timeframe=1D` | Not supported |
| `GET` | `/api/v1/stocks/{symbol}/fundamentals` | Not supported |

`/api/v1/stocks/{symbol}/fundamentals` and `/api/v1/stocks/{symbol}/valuation` return HTTP `501` because the previous yFinance fundamentals and DCF model were removed.

## Error Handling

Common responses:

| HTTP Status | Meaning |
| --- | --- |
| `200` | Request succeeded |
| `404` | Symbol or data not found |
| `422` | Request validation failed |
| `500` | Missing server dependency or configuration issue |
| `502` | Upstream provider error |
| `503` | Retryable upstream provider error |

Example error:

```json
{
  "detail": "No data found for MISSING on NASDAQ."
}
```

Client recommendations:

- Treat `404` as a symbol/data availability issue.
- Treat `422` as a request-shape bug in the client.
- Retry `503` with backoff.
- Do not aggressively retry `502`; show a temporary provider error to the user.
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

export async function getTechnicalAnalysis(exchange, symbol, timeframe = "1D") {
  return apiGet(
    `/api/v1/markets/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}/technical?timeframe=${encodeURIComponent(timeframe)}`
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
backtest = post_json(
    "/api/v1/backtests/TVC/XAUUSD",
    {"strategy": "rsi", "period": "1y", "interval": "1d"},
)

print(technical["market_sentiment"]["buy_sell_signal"])
print(backtest["symbol"], backtest["candles_analyzed"])
```

## Server Configuration

Environment variables:

```powershell
$env:STOCK_API_DEFAULT_EXCHANGE = "NASDAQ"
$env:STOCK_API_DEFAULT_TIMEFRAME = "1D"
```

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
- The API returns trading analysis, not investment advice.
- Backtest results are historical simulations and are not predictive.
- Market-wide screeners can be unavailable for markets where TradingView MCP does not provide a usable symbol universe.
- Always URL-encode `exchange` and `symbol` values in client applications.
