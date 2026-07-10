# Trading Intelligence API

FastAPI API for market quotes, yfinance-based analysis, TradingView MCP technical analysis and screeners, sentiment, news, and backtests.

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
- `GET /api/v1/markets/{exchange}/{symbol}/technical?timeframe=1D`
- `GET /api/v1/markets/{exchange}/gainers`
- `GET /api/v1/markets/{exchange}/losers`
- `GET /api/v1/markets/{exchange}/bollinger-scan`
- `GET /api/v1/markets/{exchange}/rating-filter`
- `POST /api/v1/backtests/{exchange}/{symbol}`
- `POST /api/v1/backtests/{exchange}/{symbol}/compare`
- `POST /api/v1/backtests/{exchange}/{symbol}/walk-forward`
- `GET /api/v1/sentiment/{symbol}`
- `GET /api/v1/news`

Legacy stock routes remain as compatibility aliases:

- `GET /api/v1/stocks/{symbol}/quote`
- `GET /api/v1/stocks/{symbol}/technicals`
- `POST /api/v1/stocks/{symbol}/valuation`

`GET /api/v1/stocks/{symbol}/fundamentals` and `POST /api/v1/stocks/{symbol}/valuation` return `501`; fundamentals and valuation are not part of the current API surface.

Quote responses are Yahoo-backed and include price, previous close, change, currency, market state, and 52-week high/low when Yahoo provides them.
Analysis responses are calculated locally from yfinance OHLCV history, so `/analysis` does not call TradingView's scanner endpoint. Analysis `price_data` also includes yfinance fast quote metadata such as market cap and 52-week high/low when available. The top-level `valuation_metrics` object reports trailing P/E as the primary ratio, forward P/E separately, and their supporting EPS values. Missing trailing P/E is calculated from current price and positive diluted trailing EPS when possible; unavailable or non-positive inputs remain `null` without failing the analysis response.
Technical responses come from TradingView MCP single-symbol technical analysis and include the provider's indicator objects, market sentiment, stock score when available, and trade setup fields when available.

## Markets

Exchange codes are mapped to Yahoo-compatible symbols where needed. For Singapore, use `SGX`:

- Quote: `GET /api/v1/markets/SGX/D05/quote`
- Analysis: `GET /api/v1/markets/SGX/D05/analysis?timeframe=1D`
- Technical: `GET /api/v1/markets/SGX/D05/technical?timeframe=1D`

The provider also accepts Yahoo-style Singapore symbols such as `D05.SI` for analysis and technical routes, and returns public symbols such as `SGX:D05`. Market-wide SGX scanners still depend on the symbol universe available from the TradingView MCP package.

For spot gold, use `TVC` with `XAUUSD`:

- Quote: `GET /api/v1/markets/TVC/XAUUSD/quote`
- Analysis: `GET /api/v1/markets/TVC/XAUUSD/analysis?timeframe=1D`
- Technical: `GET /api/v1/markets/TVC/XAUUSD/technical?timeframe=1D`
- Backtest: `POST /api/v1/backtests/TVC/XAUUSD`

yfinance-backed quote, analysis, and backtest data resolves this to `GC=F`. TradingView MCP technical analysis resolves `XAUUSD` to TradingView's `TVC:GOLD` feed.

## Technical Analysis

`/technical` returns the TradingView MCP technical analysis payload for one symbol. Client applications should treat indicator objects as provider-shaped dictionaries and read only the fields they need.

## Configuration

```powershell
$env:STOCK_API_DEFAULT_EXCHANGE = "NASDAQ"
$env:STOCK_API_DEFAULT_TIMEFRAME = "1D"
```

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

## Deployment

For Render deployment, use:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
