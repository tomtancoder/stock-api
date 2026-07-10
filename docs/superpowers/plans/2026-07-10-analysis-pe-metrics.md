# Analysis P/E Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return robust trailing and forward P/E metrics inside the existing yFinance-backed `/analysis` response.

**Architecture:** Add a focused `yfinance_fundamentals` service that owns metadata retrieval, TTL caching, financial-statement EPS fallback, and pure P/E normalization. The existing `yfinance_analysis` service obtains cached metadata best-effort and adds a top-level `valuation_metrics` object without changing price or technical-indicator behavior.

**Tech Stack:** Python 3.11+, FastAPI, yfinance 1.5.1+, pandas, pytest

## Global Constraints

- Keep `GET /api/v1/markets/{exchange}/{symbol}/analysis` as the single application-facing call.
- Keep the TradingView-backed `/technical` endpoint unchanged.
- Use trailing diluted P/E as primary and never substitute forward P/E into `trailing_pe`.
- Accept only finite, positive P/E and EPS values; return null for zero, negative, NaN, or infinite values.
- Fundamental lookup failures must not fail the existing analysis response.
- Cache fundamentals with `STOCK_API_CACHE_TTL_SECONDS`, which defaults to 3600 seconds.
- Preserve all pre-existing uncommitted work; do not commit overlapping dirty files as part of this implementation.

---

### Task 1: Fundamental metadata and P/E normalization service

**Files:**
- Create: `app/services/yfinance_fundamentals.py`
- Create: `tests/test_yfinance_fundamentals.py`

**Interfaces:**
- Consumes: `app.core.config.get_settings().cache_ttl_seconds`, `yfinance.Ticker.get_info()`, and `yfinance.Ticker.get_income_stmt(freq="trailing")`
- Produces: `get_valuation_metadata(symbol: str) -> dict[str, Any]`, `build_valuation_metrics(current_price: Any, metadata: dict[str, Any] | None) -> dict[str, Any]`, and `_clear_cache() -> None`

- [ ] **Step 1: Write failing pure-normalization tests**

Add tests that assert direct positive ratios are retained, missing ratios are calculated from positive price/EPS, and invalid numeric values become null:

```python
def test_build_valuation_metrics_prefers_direct_ratios():
    result = build_valuation_metrics(100, {
        "trailing_pe": 20,
        "forward_pe": 16,
        "diluted_eps_ttm": 5,
        "forward_eps": 6.25,
    })
    assert result == {
        "trailing_pe": 20.0,
        "forward_pe": 16.0,
        "diluted_eps_ttm": 5.0,
        "forward_eps": 6.25,
        "primary_pe": "trailing",
        "pe_calculated": False,
    }


def test_build_valuation_metrics_calculates_missing_ratios():
    result = build_valuation_metrics(100, {
        "diluted_eps_ttm": 4,
        "forward_eps": 5,
    })
    assert result["trailing_pe"] == 25.0
    assert result["forward_pe"] == 20.0
    assert result["pe_calculated"] is True
```

Parametrize zero, negative, `float("nan")`, and `float("inf")` inputs and assert the corresponding P/E and EPS fields are null.

- [ ] **Step 2: Run the focused tests and confirm the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_fundamentals.py -q`

Expected: collection failure because `app.services.yfinance_fundamentals` does not exist.

- [ ] **Step 3: Implement pure normalization**

Create `build_valuation_metrics` using a private finite-positive converter and round calculated ratios to four decimal places. Always return all six contract fields; set `primary_pe` to `"trailing"` and set `pe_calculated` only when trailing P/E is calculated.

- [ ] **Step 4: Run normalization tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_fundamentals.py -q`

Expected: all normalization tests pass.

- [ ] **Step 5: Write failing retrieval, statement-fallback, failure, and cache tests**

Use fake `Ticker` objects to verify:

```python
class FakeTicker:
    def get_info(self):
        return {"trailingPE": None, "forwardPE": 18, "trailingEps": None, "forwardEps": 5}

    def get_income_stmt(self, freq: str):
        assert freq == "trailing"
        return pd.DataFrame({"TTM": {"DilutedEPS": 4.0}})
```

Assert normalized metadata uses statement `DilutedEPS`, exceptions produce an empty metadata dictionary, repeated calls within the configured TTL invoke the downloader once, and a call after expiry refreshes it.

- [ ] **Step 6: Implement best-effort retrieval and TTL cache**

Implement a lock-protected in-memory cache keyed by normalized Yahoo symbol. Do not hold the lock during network I/O. Retrieve `trailingPE`, `forwardPE`, `trailingEps`, and `forwardEps` from `get_info`; call `get_income_stmt(freq="trailing")` only when a valid trailing EPS is absent. Catch provider errors so retrieval returns `{}`.

- [ ] **Step 7: Run the complete fundamental-service tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_fundamentals.py -q`

Expected: all tests pass with no network access.

### Task 2: Add valuation metrics to yFinance analysis

**Files:**
- Modify: `app/services/yfinance_analysis.py:9-42,88-220`
- Modify: `tests/test_yfinance_analysis.py`

**Interfaces:**
- Consumes: `get_valuation_metadata(symbol)` and `build_valuation_metrics(current_price, metadata)` from Task 1
- Produces: the existing `get_analysis(exchange, symbol, timeframe)` payload plus top-level `valuation_metrics`

- [ ] **Step 1: Write failing integration tests**

Stub `get_valuation_metadata` in all existing analysis tests to prevent network access. Add a test that returns direct metadata and asserts:

```python
assert response["valuation_metrics"] == {
    "trailing_pe": 22.5,
    "forward_pe": 18.0,
    "diluted_eps_ttm": 10.2,
    "forward_eps": 12.75,
    "primary_pe": "trailing",
    "pe_calculated": False,
}
```

Add a failure test where metadata retrieval returns `{}` and assert analysis still returns its price data plus the six-field null valuation object.

- [ ] **Step 2: Run the analysis tests and confirm the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_analysis.py -q`

Expected: new assertions fail because `valuation_metrics` is absent.

- [ ] **Step 3: Integrate the fundamental service**

Fetch valuation metadata after valid history is available. Pass it into `_build_analysis`, calculate metrics using the already-derived current close, and add `valuation_metrics` beside `price_data`. Do not alter empty-history or upstream-history error responses.

- [ ] **Step 4: Run focused integration and route tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_analysis.py tests\test_tradingview_provider.py tests\test_api.py -q`

Expected: all focused tests pass and `/analysis` retains the new object.

### Task 3: Document and verify the public behavior

**Files:**
- Modify: `API_DOCUMENTATION.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: final response contract from Task 2
- Produces: documented `/analysis` P/E fields and fallback semantics

- [ ] **Step 1: Update endpoint documentation**

Document the `valuation_metrics` example, trailing-primary rule, calculation fallback, nullable invalid/loss-making behavior, forward-estimate labeling, and best-effort failure behavior in the existing `/analysis` sections.

- [ ] **Step 2: Run the complete mocked suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: the complete suite passes; the live marker remains skipped unless explicitly enabled.

- [ ] **Step 3: Run cleanup verification**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n "valuation_metrics|trailing_pe|forward_pe" app tests README.md API_DOCUMENTATION.md`

Expected: the contract appears in the service, tests, and both documentation files.

- [ ] **Step 4: Review the scoped diff**

Run: `git diff -- app/services/yfinance_fundamentals.py app/services/yfinance_analysis.py tests/test_yfinance_fundamentals.py tests/test_yfinance_analysis.py README.md API_DOCUMENTATION.md`

Expected: only P/E-related additions appear within the pre-existing uncommitted files; unrelated user changes remain intact.
