# Stock API

FastAPI starter API for stock quotes, fundamentals, and intrinsic-value estimates using yFinance.

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
- `GET /api/v1/stocks/{symbol}/quote`
- `GET /api/v1/stocks/{symbol}/fundamentals`
- `POST /api/v1/stocks/{symbol}/valuation`

## Sample Ticker Test Set

The project includes 600 sample Yahoo Finance tickers in `app/ticker_universe.py`:

- 300 US tickers, such as `AAPL`, `MSFT`, and `NVDA`
- 300 Singapore tickers using Yahoo's `.SI` suffix, such as `A17U.SI`, `BN4.SI`, and `BUOU.SI`
- `SAMPLE_100_TICKERS` remains available as a smaller 50 US + 50 Singapore subset

Run the normal mocked test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run the optional live yFinance check for all 600 tickers:

```powershell
$env:RUN_LIVE_YFINANCE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_ticker_universe.py -m live -q
```

Example valuation request:

```json
{
  "discount_rate": 0.1,
  "terminal_growth_rate": 0.025,
  "projection_years": 5,
  "margin_of_safety": 0.25,
  "growth_rate": 0.08
}
```

All valuation assumptions are optional. Missing values use conservative defaults from the application settings.

## yFinance Rate Limits

Quote requests use a lightweight yFinance path and are cached for 1 hour by default. You can change the cache duration with:

```powershell
$env:STOCK_API_CACHE_TTL_SECONDS = "3600"
```

On Render, set the same value in the service environment variables.
