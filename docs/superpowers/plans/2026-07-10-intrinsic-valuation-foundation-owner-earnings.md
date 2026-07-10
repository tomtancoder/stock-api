# Intrinsic Valuation Foundation and Owner Earnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a working fundamentals-based `/valuation` endpoint for ordinary US and SGX operating companies using a transparent three-scenario owner-earnings DCF.

**Architecture:** Extract shared market-symbol normalization, introduce normalized valuation types and provider boundaries, then keep SEC/yFinance retrieval separate from pure valuation math. A valuation service combines cached fundamentals with the existing quote provider and exposes one typed FastAPI route; banks and REITs are recognized but remain `valuation_unreliable` until the next plans add their engines.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pydantic-settings, httpx, pandas, yfinance 1.5.1+, pytest

## Global Constraints

- Add `GET /api/v1/markets/{exchange}/{symbol}/valuation` and keep `/technical`, `/analysis`, and `/quote` behavior unchanged.
- Keep legacy `POST /api/v1/stocks/{symbol}/valuation` returning `501`.
- Accept both `SGX/D05` and `SGX/D05.SI`, use `.SI` for Yahoo access, and return `SGX:D05` publicly.
- Use SEC EDGAR as primary US fundamentals when `STOCK_API_SEC_USER_AGENT` is configured; otherwise fall back to yFinance with an explicit warning and no high-confidence result.
- Use yFinance fundamentals for SGX and cap confidence at `medium`.
- Never silently replace missing facts with zero or combine incompatible currencies or units.
- Keep provider I/O out of deterministic model functions.
- Calculate intrinsic values once daily, refresh current prices independently, and preserve usable stale valuations when refresh fails.
- Treat stock-based compensation as an owner expense and subtract IFRS financing-classified interest exactly once.
- Use a 25% margin of safety and require finite positive values with `bear <= base <= bull`.
- Keep normal tests fully mocked; live-provider checks remain opt-in through the existing `live` marker.
- Preserve unrelated user changes and commit only files named by each task.

## File Map

- `app/services/market_symbols.py`: shared exchange, Yahoo-symbol, and public-symbol normalization.
- `app/services/valuation_types.py`: normalized provider facts and pure model result types.
- `app/services/valuation_math.py`: finite-number helpers, discounting, scenario validation, and price classification.
- `app/services/owner_earnings_valuation.py`: ordinary-company normalization and DCF.
- `app/services/yfinance_statements.py`: annual/quarterly/TTM Yahoo statement normalization.
- `app/services/sec_companyfacts.py`: SEC ticker-to-CIK lookup and Company Facts normalization.
- `app/services/valuation_fundamentals.py`: source precedence, daily fundamentals cache, and stale behavior.
- `app/services/valuation_router.py`: company-type classification and model dispatch.
- `app/services/valuation_service.py`: quote/fundamentals orchestration and public response construction.
- `app/schemas.py`: typed public valuation response.
- `app/api/v1/markets.py`: canonical route and error mapping.
- Focused tests mirror each new service under `tests/`.

---

### Task 1: Shared market-symbol normalization

**Files:**
- Create: `app/services/market_symbols.py`
- Create: `tests/test_market_symbols.py`
- Modify: `app/services/yfinance_analysis.py:14-16,301-318`
- Modify: `app/services/tradingview_provider.py:1-8,403-409`
- Test: `tests/test_yfinance_analysis.py`
- Test: `tests/test_tradingview_provider.py`

**Interfaces:**
- Consumes: raw `exchange` and `symbol` path values.
- Produces: `normalize_exchange(exchange: str) -> str`, `to_yahoo_symbol(exchange: str, symbol: str) -> str`, and `to_public_symbol(exchange: str, symbol: str) -> str`.

- [ ] **Step 1: Write failing symbol-normalization tests**

Create `tests/test_market_symbols.py`:

```python
from app.services.market_symbols import (
    normalize_exchange,
    to_public_symbol,
    to_yahoo_symbol,
)


def test_sgx_symbol_round_trip():
    assert normalize_exchange(" sgx ") == "SGX"
    assert to_yahoo_symbol("SGX", "d05") == "D05.SI"
    assert to_yahoo_symbol("SGX", "D05.SI") == "D05.SI"
    assert to_public_symbol("SGX", "D05.SI") == "SGX:D05"


def test_gold_alias_keeps_existing_yahoo_mapping():
    assert to_yahoo_symbol("TVC", "XAUUSD") == "GC=F"
    assert to_public_symbol("TVC", "GOLD") == "TVC:XAUUSD"


def test_normal_symbols_are_uppercased_without_suffixes():
    assert to_yahoo_symbol("nasdaq", "aapl") == "AAPL"
    assert to_public_symbol("nasdaq", "aapl") == "NASDAQ:AAPL"
```

- [ ] **Step 2: Run the tests to verify the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_market_symbols.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.market_symbols'`.

- [ ] **Step 3: Implement the shared resolver**

Create `app/services/market_symbols.py`:

```python
def normalize_exchange(exchange: str) -> str:
    return exchange.strip().upper()


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def to_yahoo_symbol(exchange: str, symbol: str) -> str:
    venue = normalize_exchange(exchange)
    normalized = _normalize_symbol(symbol)
    if venue in {"TVC", "CAPITALCOM"} and normalized in {
        "XAUUSD",
        "GOLD",
        "TVC:GOLD",
    }:
        return "GC=F"
    if venue == "SGX" and not normalized.endswith(".SI"):
        return f"{normalized}.SI"
    return normalized


def to_public_symbol(exchange: str, symbol: str) -> str:
    venue = normalize_exchange(exchange)
    normalized = _normalize_symbol(symbol)
    if venue == "SGX" and normalized.endswith(".SI"):
        normalized = normalized[:-3]
    if venue in {"TVC", "CAPITALCOM"} and normalized in {
        "GOLD",
        "TVC:GOLD",
        "GC=F",
    }:
        normalized = "XAUUSD"
    return f"{venue}:{normalized}"
```

- [ ] **Step 4: Replace duplicate Yahoo/public normalization calls**

In `app/services/yfinance_analysis.py`, import `to_public_symbol` and `to_yahoo_symbol`, replace calls to `_yahoo_symbol` and `_public_symbol`, then remove those two private functions. In `app/services/tradingview_provider.py`, make `_quote_symbol` return `to_yahoo_symbol(exchange, symbol)` and retain TradingView-only suffix stripping in `_analysis_symbol`.

Use these exact imports:

```python
from app.services.market_symbols import to_public_symbol, to_yahoo_symbol
```

- [ ] **Step 5: Run focused regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_market_symbols.py tests\test_yfinance_analysis.py tests\test_tradingview_provider.py -q`

Expected: all tests pass and existing SGX, gold, quote, analysis, and technical symbol behavior remains unchanged.

- [ ] **Step 6: Commit the shared symbol seam**

```powershell
git add app/services/market_symbols.py app/services/yfinance_analysis.py app/services/tradingview_provider.py tests/test_market_symbols.py tests/test_yfinance_analysis.py tests/test_tradingview_provider.py
git commit -m "Share market symbol normalization"
```

---

### Task 2: Normalized valuation types and common math

**Files:**
- Create: `app/services/valuation_types.py`
- Create: `app/services/valuation_math.py`
- Create: `tests/test_valuation_math.py`
- Modify: `app/schemas.py`

**Interfaces:**
- Consumes: provider facts expressed in normalized currency units.
- Produces: `FactProvenance`, `FinancialPeriod`, `ValuationFundamentals`, `ScenarioAssumptions`, `ModelResult`, `PriceClassification`, `classify_price(current_price: float, *, bear: float, base: float, bull: float, margin_of_safety: float = 0.25) -> PriceClassification`, and `validate_scenarios(bear: float, base: float, bull: float) -> None`.

- [ ] **Step 1: Write failing classification and invariant tests**

Create `tests/test_valuation_math.py`:

```python
import pytest

from app.services.valuation_math import classify_price, validate_scenarios


def test_price_classification_uses_approved_precedence():
    assert classify_price(70, bear=80, base=100, bull=130).status == "cheap"
    assert classify_price(75, bear=80, base=100, bull=130).status == "cheap"
    assert classify_price(100, bear=80, base=100, bull=130).status == "fair"
    assert classify_price(111, bear=80, base=100, bull=130).status == "expensive"
    assert classify_price(131, bear=80, base=100, bull=130).status == "very_expensive"


def test_price_classification_reports_ratios():
    result = classify_price(80, bear=70, base=100, bull=120)
    assert result.margin_of_safety_price == 75.0
    assert result.price_to_base_value == 0.8
    assert result.upside_downside_percent == 25.0


@pytest.mark.parametrize(
    "values",
    [(100, 90, 120), (0, 100, 120), (80, 100, float("inf"))],
)
def test_validate_scenarios_rejects_invalid_ranges(values):
    with pytest.raises(ValueError):
        validate_scenarios(*values)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_valuation_math.py -q`

Expected: collection fails because valuation modules do not exist.

- [ ] **Step 3: Create normalized internal types**

Create `app/services/valuation_types.py` with these concrete models:

```python
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    concept: str | None = None
    form: str | None = None
    accession: str | None = None
    period_end: date | None = None
    filed_at: date | None = None
    unit: str | None = None


class FinancialPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)
    period_end: date
    fiscal_year: int | None = None
    is_ttm: bool = False
    currency: str
    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    stock_based_compensation: float | None = None
    interest_paid_outside_operating: float | None = None
    revenue: float | None = None
    net_income_common: float | None = None
    common_equity: float | None = None
    cash_and_equivalents: float | None = None
    total_assets: float | None = None
    total_debt: float | None = None
    diluted_shares: float | None = None
    common_dividends: float | None = None
    distribution_per_unit: float | None = None
    nav_per_unit: float | None = None
    sources: dict[str, FactProvenance] = Field(default_factory=dict)


class ValuationFundamentals(BaseModel):
    symbol: str
    exchange: str
    currency: str
    primary_source: str
    provider_security_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    issuer_classification: str | None = None
    current_diluted_shares: float | None = None
    periods: list[FinancialPeriod] = Field(default_factory=list)
    fetched_at: datetime
    sources: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScenarioAssumptions(BaseModel):
    name: Literal["bear", "base", "bull"]
    starting_factor: float
    initial_growth: float
    required_return: float
    terminal_growth: float
    projection_years: int = 10


class ModelResult(BaseModel):
    method: str
    detected_company_type: str
    bear: float
    base: float
    bull: float
    details: dict[str, object]
    assumptions: dict[str, object]
    quality: dict[str, object]
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Implement common price math**

Create `app/services/valuation_math.py`:

```python
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceClassification:
    status: str
    margin_of_safety_price: float
    price_to_base_value: float
    upside_downside_percent: float


def _positive_finite(value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("valuation values must be finite and positive")
    return number


def validate_scenarios(bear: float, base: float, bull: float) -> None:
    low = _positive_finite(bear)
    middle = _positive_finite(base)
    high = _positive_finite(bull)
    if not low <= middle <= high:
        raise ValueError("valuation scenarios must satisfy bear <= base <= bull")


def classify_price(
    current_price: float,
    *,
    bear: float,
    base: float,
    bull: float,
    margin_of_safety: float = 0.25,
) -> PriceClassification:
    price = _positive_finite(current_price)
    validate_scenarios(bear, base, bull)
    if not 0 <= margin_of_safety < 1:
        raise ValueError("margin_of_safety must be between 0 and 1")
    margin_price = base * (1 - margin_of_safety)
    if price > bull:
        status = "very_expensive"
    elif price > base * 1.10:
        status = "expensive"
    elif price <= margin_price:
        status = "cheap"
    else:
        status = "fair"
    return PriceClassification(
        status=status,
        margin_of_safety_price=round(margin_price, 4),
        price_to_base_value=round(price / base, 4),
        upside_downside_percent=round((base - price) / price * 100, 2),
    )
```

- [ ] **Step 5: Add the public response schema**

Append Pydantic models to `app/schemas.py` for `IntrinsicValueRange`, `ValuationDataQuality`, `ValuationQuality`, `OwnerEarningsValuationDetails`, and `ValuationResponse`. Use `dict[str, Any]` only for scenario, quality-detail, and source maps; keep top-level fields typed and set `response_model=ValuationResponse` in Task 7.

Define owner details with `method: Literal["owner_earnings_dcf"]`, normalized owner earnings, owner earnings per share, maintenance-capex method, annual history, derived growth, and usable years. Define `ValuationResponse.method`, `ValuationResponse.confidence`, `ValuationResponse.intrinsic_value`, and `ValuationResponse.model_details` as nullable so a typed `valuation_unreliable` response can omit unsupported claims and numbers. When present, confidence is restricted to `high`, `medium`, or `low`. The top-level response fields must exactly match the approved design: symbol, exchange, currency, detected company type, method, classification sources, status, confidence, current price, price timestamp, intrinsic values, model details, quality, assumptions, data quality, sources, and warnings.

- [ ] **Step 6: Run common tests and schema import check**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_valuation_math.py -q`

Run: `.\.venv\Scripts\python.exe -c "from app.schemas import ValuationResponse; print(ValuationResponse.model_json_schema()['title'])"`

Expected: tests pass and the command prints `ValuationResponse`.

- [ ] **Step 7: Commit shared valuation contracts**

```powershell
git add app/services/valuation_types.py app/services/valuation_math.py app/schemas.py tests/test_valuation_math.py
git commit -m "Add valuation contracts and price math"
```

---

### Task 3: Pure owner-earnings valuation engine

**Files:**
- Create: `app/services/owner_earnings_valuation.py`
- Create: `tests/test_owner_earnings_valuation.py`

**Interfaces:**
- Consumes: `ValuationFundamentals` with at least three usable ordinary-company periods.
- Produces: `calculate_period_owner_earnings(period: FinancialPeriod) -> float | None`, `normalize_owner_earnings(fundamentals: ValuationFundamentals) -> float`, and `value_owner_earnings(fundamentals: ValuationFundamentals) -> ModelResult`.

- [ ] **Step 1: Write failing period-normalization tests**

Create fixtures with USD and SGD periods and assert:

```python
def test_owner_earnings_subtracts_sbc_and_external_interest_once(period_factory):
    period = period_factory(
        operating_cash_flow=120,
        capital_expenditure=-30,
        stock_based_compensation=10,
        interest_paid_outside_operating=5,
    )
    assert calculate_period_owner_earnings(period) == 75.0


def test_owner_earnings_does_not_subtract_working_capital_again(period_factory):
    period = period_factory(
        operating_cash_flow=100,
        capital_expenditure=20,
        stock_based_compensation=0,
        interest_paid_outside_operating=0,
    )
    assert calculate_period_owner_earnings(period) == 80.0
```

Also assert missing operating cash flow or capex returns `None`, while missing SBC and external interest remain missing facts that the caller must resolve before invoking the pure engine.

- [ ] **Step 2: Run the focused tests to confirm failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_owner_earnings_valuation.py -q`

Expected: collection fails because `owner_earnings_valuation` does not exist.

- [ ] **Step 3: Implement period and starting-value normalization**

Implement these rules exactly:

```python
def calculate_period_owner_earnings(period: FinancialPeriod) -> float | None:
    required = (period.operating_cash_flow, period.capital_expenditure)
    if any(value is None for value in required):
        return None
    if period.stock_based_compensation is None:
        return None
    if period.interest_paid_outside_operating is None:
        return None
    return (
        float(period.operating_cash_flow)
        - abs(float(period.capital_expenditure))
        - abs(float(period.stock_based_compensation))
        - abs(float(period.interest_paid_outside_operating))
    )
```

For normalization, sort annual periods by `period_end`, require at least three positive usable owner-earnings periods, use weights `1, 2, 3` for the last three periods, and calculate the median of available TTM owner earnings, weighted three-year owner earnings, and five-year median owner-earnings margin times trailing revenue. Require at least two components.

- [ ] **Step 4: Add failing normalization and scenario tests**

Assert five-year inputs produce the expected median starting value, two independent components are required, base growth accepts declining per-share history down to `-0.15`, and every result satisfies `bear <= base <= bull`.

Use a stable fixture where normalized owner earnings are `1000`, shares are `100`, and derived growth is `0.04`; assert the model returns finite positive per-share values, `method == "owner_earnings_dcf"`, and all approved assumptions in `result.assumptions`.

- [ ] **Step 5: Implement growth, fade, DCF, and scenario validation**

Implement per-share revenue and owner-earnings CAGR candidates from the earliest and latest positive comparable periods. Use the median candidate, clamped to `[-0.15, 0.12]`.

Build assumptions as follows:

```python
bear_growth = max(-0.20, base_growth - 0.04)
bull_growth = min(0.15, base_growth + 0.03)
scenarios = (
    ScenarioAssumptions(
        name="bear",
        starting_factor=0.90,
        initial_growth=bear_growth,
        required_return=0.12,
        terminal_growth=0.02,
    ),
    ScenarioAssumptions(
        name="base",
        starting_factor=1.00,
        initial_growth=base_growth,
        required_return=0.10,
        terminal_growth=0.025,
    ),
    ScenarioAssumptions(
        name="bull",
        starting_factor=1.05,
        initial_growth=bull_growth,
        required_return=0.08,
        terminal_growth=0.03,
    ),
)
```

Fade growth linearly from initial growth toward terminal growth across ten years. Discount each projected owner-earnings amount, calculate `OE10 * (1 + g) / (r - g)`, discount terminal value from year ten, divide by current diluted shares, round per-share results to four decimals, and call `validate_scenarios` before returning `ModelResult`.

- [ ] **Step 6: Run pure engine tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_owner_earnings_valuation.py tests\test_valuation_math.py -q`

Expected: all tests pass with no network access.

- [ ] **Step 7: Commit the owner-earnings engine**

```powershell
git add app/services/owner_earnings_valuation.py tests/test_owner_earnings_valuation.py
git commit -m "Add owner earnings valuation engine"
```

---

### Task 4: yFinance statement provider for US fallback and SGX

**Files:**
- Create: `app/services/yfinance_statements.py`
- Create: `tests/test_yfinance_statements.py`

**Interfaces:**
- Consumes: `yfinance.Ticker` yearly, quarterly, and trailing statements for a Yahoo symbol.
- Produces: `fetch_yfinance_fundamentals(exchange: str, symbol: str) -> ValuationFundamentals` and `YFinanceStatementsError`.

- [ ] **Step 1: Write failing provider-normalization tests**

Use fake DataFrames to cover these exact row aliases:

- Operating cash flow: `Operating Cash Flow`, `Total Cash From Operating Activities`.
- Capex: `Capital Expenditure`, `Capital Expenditures`.
- SBC: `Stock Based Compensation`, `Share Based Compensation`.
- Financing-classified interest: `Interest Paid Supplemental`, `Interest Paid` only when provider metadata says it is outside CFO.
- Revenue: `Total Revenue`, `Revenue`.
- Net income: `Net Income Common Stockholders`, `Net Income`.
- Common equity: `Stockholders Equity`, `Common Stock Equity`, `Total Stockholder Equity`.
- Cash: `Cash And Cash Equivalents`, `Cash Cash Equivalents And Short Term Investments`.
- Total assets: `Total Assets`.
- Total debt: `Total Debt`, `Long Term Debt And Capital Lease Obligation`.
- Diluted shares: `Diluted Average Shares`, `Weighted Average Number Of Diluted Shares Outstanding`.
- Common dividends: `Cash Dividends Paid`, `Common Stock Dividend Paid`.

Assert `fetch_yfinance_fundamentals("SGX", "D05")` constructs the provider ticker with `D05.SI`, returns public symbol `SGX:D05`, preserves `SGD`, sorts periods, records row names in provenance, and never turns an absent row into zero.

- [ ] **Step 2: Run the tests to confirm the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_statements.py -q`

Expected: collection fails because the provider module is missing.

- [ ] **Step 3: Implement normalized statement extraction**

Create a provider that calls `get_cashflow(freq="yearly")`, `get_income_stmt(freq="yearly")`, `get_balance_sheet(freq="yearly")`, trailing statement variants when available, `get_shares_full()`, `get_info()`, and `fast_info`. Build periods by normalized period end, accept only finite numeric values, retain source row names, and infer the statement currency from financial currency before quote currency. Set `interest_paid_outside_operating=0.0` only when provider accounting metadata or a complete cash-flow reconciliation confirms interest is already inside CFO, and record provenance as `included_in_operating_cash_flow`; otherwise retain `None` unless a financing-classified interest amount is available.

Use `to_yahoo_symbol` and `to_public_symbol`; do not duplicate SGX logic. Set `primary_source="yfinance_sgx"` for SGX and `primary_source="yfinance_fallback"` otherwise.

- [ ] **Step 4: Add failure and unit-consistency tests**

Assert provider exceptions raise `YFinanceStatementsError`, mismatched statement currencies are recorded as warnings and missing required normalized facts, and duplicated/amended columns select the latest provider period without adding values together.

- [ ] **Step 5: Run provider tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_statements.py -q`

Expected: all tests pass without real Yahoo calls.

- [ ] **Step 6: Commit the yFinance provider**

```powershell
git add app/services/yfinance_statements.py tests/test_yfinance_statements.py
git commit -m "Normalize yFinance valuation statements"
```

---

### Task 5: SEC Company Facts provider

**Files:**
- Create: `app/services/sec_companyfacts.py`
- Create: `tests/test_sec_companyfacts.py`
- Modify: `app/core/config.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: SEC `company_tickers.json`, submissions metadata, and Company Facts JSON.
- Produces: `fetch_sec_fundamentals(exchange: str, symbol: str) -> ValuationFundamentals`, `resolve_cik(symbol: str) -> str`, `SecCompanyFactsError`, and `_clear_cache() -> None`.

- [ ] **Step 1: Add failing configuration and CIK tests**

Test that `Settings(sec_user_agent="stock-api test@example.com")` exposes the value, `resolve_cik("AAPL")` zero-pads a mocked CIK to ten digits, and missing tickers raise `SecCompanyFactsError` with `status_code=404`.

- [ ] **Step 2: Promote httpx and add SEC settings**

Move `httpx>=0.27.0` from optional dev dependencies to main dependencies in `pyproject.toml`. Change the existing `live` marker description to cover any opt-in network-dependent provider test. Add these fields to `Settings`:

```python
valuation_cache_ttl_seconds: int = Field(default=86_400, ge=60)
valuation_quote_ttl_seconds: int = Field(default=300, ge=1)
valuation_stale_ttl_seconds: int = Field(default=604_800, ge=300)
sec_user_agent: str | None = Field(default=None, min_length=3)
```

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`

Expected: editable installation succeeds with httpx available at runtime.

- [ ] **Step 3: Implement SEC HTTP and ticker mapping**

Use an `httpx.Client(timeout=20.0, headers={"User-Agent": settings.sec_user_agent})`. Refuse a live SEC request when `sec_user_agent` is absent so the facade can choose yFinance fallback. Cache ticker mapping for `valuation_cache_ttl_seconds`; do not hold the cache lock during HTTP I/O.

Use:

```python
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
```

- [ ] **Step 4: Write failing Company Facts selection tests**

Build fixtures containing duplicate facts, amendments, annual and quarterly frames, different units, and alternative concepts. Assert selection prefers the latest filed non-amended annual fact for a period unless an amendment is later and compatible, never sums duplicate frames, keeps USD and shares separate, and records accession/form/filed/unit/concept provenance.

Cover alternative concepts for operating cash flow, capex, SBC, interest paid, revenue, net income common, common equity, diluted shares, and common dividends.

- [ ] **Step 5: Implement Company Facts normalization**

Create concept maps ordered from most specific to acceptable fallback. Filter forms to `10-K`, `10-K/A`, `10-Q`, and `10-Q/A`; build five annual periods plus a TTM period when four compatible quarters exist; use submission metadata for entity classification. Return `primary_source="sec_companyfacts"` and preserve missing fields rather than substituting Yahoo data inside this provider.

- [ ] **Step 6: Run SEC provider tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py -q`

Expected: all SEC tests pass with mocked HTTP responses.

- [ ] **Step 7: Commit SEC retrieval**

```powershell
git add app/services/sec_companyfacts.py app/core/config.py pyproject.toml tests/test_sec_companyfacts.py
git commit -m "Add SEC valuation fundamentals provider"
```

---

### Task 6: Fundamentals facade, daily cache, and ordinary-company routing

**Files:**
- Create: `app/services/valuation_fundamentals.py`
- Create: `app/services/valuation_router.py`
- Create: `tests/test_valuation_fundamentals.py`
- Create: `tests/test_valuation_router.py`

**Interfaces:**
- Consumes: SEC and yFinance normalized provider results.
- Produces: `FundamentalsEnvelope`, `CompanyClassification`, `ValuationUnreliable`, `get_fundamentals(exchange: str, symbol: str) -> FundamentalsEnvelope`, `classify_company(fundamentals: ValuationFundamentals) -> CompanyClassification`, `route_valuation(fundamentals: ValuationFundamentals) -> ModelResult`, and cache reset helpers for tests.

- [ ] **Step 1: Write failing source-precedence and cache tests**

Assert:

- NASDAQ uses SEC first when configured.
- SEC missing facts are filled from a same-currency yFinance fallback only at the facade, with field-level sources retained.
- Missing SEC configuration uses yFinance and adds a warning.
- SGX goes directly to yFinance and confidence cannot become high.
- Fresh repeated calls invoke providers once.
- Expired refresh failure returns the stale entry within `valuation_stale_ttl_seconds`.
- No stale entry propagates a typed provider failure.

- [ ] **Step 2: Implement the fundamentals envelope and cache**

Use immutable `FundamentalsEnvelope(fundamentals, fresh_until, stale_until, stale, warnings)` and a lock-protected cache keyed by `(normalized_exchange, public_symbol)`. Store normalized fundamentals and source warnings. Perform provider I/O outside the lock. Merge only facts with matching currency, period end, and unit; never overwrite a primary fact with a fallback fact.

- [ ] **Step 3: Write failing company-classification tests**

Use fixtures for an operating company, a bank, a REIT, an insurer, and conflicting metadata. Assert ordinary-company evidence routes to `owner_earnings_dcf`; bank and REIT evidence return recognized classifications with `supported=False` in this plan; insurer and conflict return `unsupported` or `ambiguous`.

- [ ] **Step 4: Implement the router**

Define immutable `CompanyClassification(company_type, supported, sources, reasons)`. Define `ValuationUnreliable(RuntimeError)` with a `reasons: list[str]` attribute. Give explicit REIT type precedence, require both bank-industry metadata and bank-like statements for bank classification, and use ordinary-company routing only when compatible cash-flow/revenue facts exist. `route_valuation` calls `value_owner_earnings` only for supported operating companies and raises `ValuationUnreliable` with reasons otherwise.

- [ ] **Step 5: Run facade and router tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_valuation_fundamentals.py tests\test_valuation_router.py -q`

Expected: all tests pass with provider calls mocked.

- [ ] **Step 6: Commit provider orchestration and routing**

```powershell
git add app/services/valuation_fundamentals.py app/services/valuation_router.py tests/test_valuation_fundamentals.py tests/test_valuation_router.py
git commit -m "Route cached valuation fundamentals"
```

---

### Task 7: Valuation service and canonical market endpoint

**Files:**
- Create: `app/services/valuation_service.py`
- Create: `tests/test_valuation_service.py`
- Modify: `app/api/v1/markets.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `get_fundamentals`, `classify_company`, `route_valuation`, and existing `tradingview_provider.get_quote`.
- Produces: `get_valuation(exchange: str, symbol: str) -> ValuationResponse`, `_clear_valuation_caches() -> None`, and the public GET route.

- [ ] **Step 1: Write failing service response tests**

Stub fundamentals, model result, and quote. Assert the service returns public symbol, exchange, currency, owner method, scenario values, classification, confidence, assumptions, owner detail fields, source map, `financials_as_of`, separate `price_as_of`, and warnings.

Add tests for quote/fundamentals currency mismatch, missing quote price, recognized bank/REIT returning `valuation_unreliable`, stale data, and provider failures. Add fake-clock tests proving model results are reused for 24 hours while quotes refresh after five minutes, and a changed fundamentals `fetched_at` or model version invalidates only the model-result cache.

- [ ] **Step 2: Implement service orchestration**

Define `VALUATION_MODEL_VERSION = "1"`. Cache pure `ModelResult` values by normalized symbol, model version, and fundamentals `fetched_at` until the fundamentals envelope's `fresh_until`. Cache quote payloads separately for `valuation_quote_ttl_seconds`. Fetch cached fundamentals, route or reuse the model result, fetch or reuse the quote independently, compare normalized three-letter currencies, call `classify_price`, and build `ValuationResponse`. Map provider/source quality to confidence using the approved high/medium/low rules. Use UTC ISO timestamps and compute `next_refresh_at` from the fundamentals cache entry.

Catch `ValuationUnreliable` inside the service and build a valid `ValuationResponse` with `status="valuation_unreliable"`, nullable intrinsic/model details, classification reasons, sources, freshness, and warnings. Do not turn this supported `200` outcome into an HTTP exception.

Define typed exceptions with `status_code`, `retry_after_s`, and reasons so the route does not depend on TradingView-specific exception classes.

- [ ] **Step 3: Write failing API route tests**

In `tests/test_api.py`, monkeypatch `markets.valuation_service.get_valuation` and assert:

```python
response = client.get("/api/v1/markets/SGX/S63/valuation")
assert response.status_code == 200
assert response.json()["symbol"] == "SGX:S63"
```

Add `404`, `502` with `Retry-After`, and `200 valuation_unreliable` tests. Assert the legacy stock POST remains `501` and technical mocks receive no valuation calls.

- [ ] **Step 4: Add the canonical route**

Import the service module as `valuation_service` and add before market-wide routes:

```python
@router.get(
    "/{exchange}/{symbol}/valuation",
    response_model=ValuationResponse,
)
def valuation(
    exchange: str = Path(..., min_length=1, max_length=32),
    symbol: str = Path(..., min_length=1, max_length=64),
) -> ValuationResponse:
    try:
        return valuation_service.get_valuation(exchange, symbol)
    except valuation_service.ValuationServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        ) from exc
```

- [ ] **Step 5: Run service and API tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_valuation_service.py tests\test_api.py -q`

Expected: all valuation tests pass and existing route tests remain green.

- [ ] **Step 6: Commit the public ordinary-company endpoint**

```powershell
git add app/services/valuation_service.py app/api/v1/markets.py tests/test_valuation_service.py tests/test_api.py
git commit -m "Expose owner earnings valuation endpoint"
```

---

### Task 8: Documentation and foundation verification

**Files:**
- Modify: `README.md`
- Modify: `API_DOCUMENTATION.md`
- Create: `tests/test_live_valuation.py`

**Interfaces:**
- Consumes: the working ordinary-company endpoint.
- Produces: documented behavior and opt-in live smoke coverage.

- [ ] **Step 1: Document the ordinary-company endpoint**

Add US and SGX request examples, the owner-earnings formula, scenario assumptions, margin-of-safety labels, freshness fields, provenance, SEC user-agent configuration, yFinance fallback warning, stale behavior, bank/REIT recognized-but-not-yet-supported behavior, and the separation from `/technical`.

- [ ] **Step 2: Add opt-in live smoke tests**

Create `tests/test_live_valuation.py` with `@pytest.mark.live` and a `RUN_LIVE_VALUATION_TESTS=1` skip gate. Check one US ordinary company and one SGX ordinary company. Assert only schema, finite positive scenario values, currency, sources, and scenario ordering; do not hard-code market prices.

- [ ] **Step 3: Run all focused valuation tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_market_symbols.py tests\test_valuation_math.py tests\test_owner_earnings_valuation.py tests\test_yfinance_statements.py tests\test_sec_companyfacts.py tests\test_valuation_fundamentals.py tests\test_valuation_router.py tests\test_valuation_service.py tests\test_api.py -q`

Expected: all focused tests pass without network access.

- [ ] **Step 4: Run the complete mocked suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: the complete suite passes; live valuation and TradingView tests remain skipped unless enabled.

- [ ] **Step 5: Run cleanup and contract verification**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n "markets/.*/valuation|owner_earnings_dcf|valuation_unreliable|SEC_USER_AGENT|SGX" app tests README.md API_DOCUMENTATION.md`

Expected: route, method, error semantics, configuration, and SGX coverage appear in implementation, tests, and docs.

- [ ] **Step 6: Commit documentation and live smoke coverage**

```powershell
git add README.md API_DOCUMENTATION.md tests/test_live_valuation.py
git commit -m "Document owner earnings valuation"
```

## Plan Completion Gate

Do not start the bank plan until:

- The canonical route returns a typed ordinary-company valuation for mocked US and SGX fixtures.
- Recognized banks and REITs return `valuation_unreliable` without invoking owner-earnings DCF.
- SEC/yFinance source precedence, daily/stale caching, field provenance, and SGD handling have focused coverage.
- The complete mocked suite and `git diff --check` pass.
