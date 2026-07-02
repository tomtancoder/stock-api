# Repository Guidelines

## Project Structure & Module Organization

This is a FastAPI stock valuation API. Source code lives in `app/`. API routes are under `app/api/v1/`, shared settings are in `app/core/`, request/response models are in `app/schemas.py`, and business logic is in `app/services/`. `app/services/yfinance_client.py` handles yFinance access and caching; `app/services/valuation.py` contains deterministic valuation calculations. The sample US/Singapore ticker universe is in `app/ticker_universe.py`. Tests live in `tests/`.

## Build, Test, and Development Commands

Create and install the local environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the API locally:

```powershell
uvicorn app.main:app --reload
```

Run the normal test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run the optional live yFinance ticker check:

```powershell
$env:RUN_LIVE_YFINANCE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_ticker_universe.py -m live -q
```

## Coding Style & Naming Conventions

Use Python 3.11+ syntax and 4-space indentation. Prefer typed function signatures, small service functions, and Pydantic models for API data. Keep route handlers thin: routes should validate input, call service functions, and return schemas. Use descriptive snake_case for functions, variables, and test names. Avoid ad hoc parsing where structured APIs or Pydantic models are available.

## Testing Guidelines

Tests use `pytest` and FastAPI `TestClient`. Name tests `test_*.py` and functions `test_*`. Mock yFinance in normal tests so CI and local development do not depend on live market data. Mark network-dependent checks with `@pytest.mark.live` and keep them opt-in through `RUN_LIVE_YFINANCE_TESTS=1`.

## Commit & Pull Request Guidelines

Current commits use short imperative messages, for example `Improve deployed yFinance fallback`. Follow that style. Pull requests should explain the API behavior changed, include validation output, and note any yFinance rate-limit or deployment implications. Link related issues when available.

## Security & Configuration Tips

Do not commit `.env`, `.venv`, API keys, or generated caches. Cache duration is controlled by `STOCK_API_CACHE_TTL_SECONDS`; use `1800` for 30 minutes or `3600` for 1 hour. For Render deployment, use:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
