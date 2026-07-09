# Trading Intelligence API

FastAPI API for market quotes, technical analysis, trade scores, screeners, sentiment, news, and backtests powered by TradingView MCP.

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
- `GET /api/v1/markets/{exchange}/{symbol}/score?timeframe=1D`
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

`GET /api/v1/stocks/{symbol}/fundamentals` returns `501` because TradingView MCP does not provide the cash flow, balance sheet, and income statement fields that powered the previous DCF model.

## Markets

Exchange codes are passed through to TradingView MCP. For Singapore, use `SGX`:

- Quote: `GET /api/v1/markets/SGX/D05/quote`
- Analysis: `GET /api/v1/markets/SGX/D05/analysis?timeframe=1D`
- Score: `GET /api/v1/markets/SGX/D05/score?timeframe=1D`

The provider also accepts Yahoo-style Singapore symbols such as `D05.SI` for analysis and score routes, and normalizes them to TradingView's `SGX:D05` format internally. Market-wide SGX scanners depend on the symbol universe available from the TradingView MCP package.

For spot gold, use `TVC` with `XAUUSD`:

- Quote: `GET /api/v1/markets/TVC/XAUUSD/quote`
- Analysis: `GET /api/v1/markets/TVC/XAUUSD/analysis?timeframe=1D`
- Score: `GET /api/v1/markets/TVC/XAUUSD/score?timeframe=1D`
- Backtest: `POST /api/v1/backtests/TVC/XAUUSD`

TradingView analysis resolves this to `TVC:GOLD`; Yahoo-backed quote and backtest data resolves to `GC=F`.

## Trade Scores

`/score` and the legacy `/valuation` route return a `TradeScoreResponse`, not an intrinsic value:

- `score` is 0-100.
- `score_source` is `stock_score` when TradingView MCP provides one.
- Otherwise, `score_source` is `technical_rating`, mapped from TradingView's `-3..3` technical rating into `0..100`.
- The response includes signal, grade, trend state, price data, trade setup, risk/reward, key indicators, and warnings.

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

Run optional live TradingView MCP checks only when explicitly enabled:

```powershell
$env:RUN_LIVE_TRADINGVIEW_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live -q
```

## Deployment

For Render deployment, use:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
