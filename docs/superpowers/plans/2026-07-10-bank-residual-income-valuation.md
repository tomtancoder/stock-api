# Bank Residual Income Valuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the canonical valuation endpoint so US and SGX banks receive bear, base, and bull residual-income valuations instead of `valuation_unreliable`.

**Architecture:** Add one pure bank engine that consumes the normalized common-equity, earnings, dividend, and share facts created by the foundation plan. Update routing and public model details without changing source precedence, caching, quote handling, or technical analysis.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pandas, pytest

## Global Constraints

- Complete `2026-07-10-intrinsic-valuation-foundation-owner-earnings.md` first.
- Use residual income; never run owner-earnings DCF for a detected bank.
- Calculate `intrinsic equity = current common equity + PV(future excess returns)`.
- Calculate annual excess return as `(ROE - required return) * beginning common equity`.
- Project common equity through retained earnings and fade ROE to the required return by year 10.
- Assign no persistent terminal excess return after year 10.
- Use 12%, 10%, and 8% required returns for bear, base, and bull.
- Reject payout ratios outside 0% to 100%; do not clamp invalid values silently.
- Require positive common equity and at least three usable years; prefer five.
- Optional CET1, NPL, and loan-loss coverage metrics affect confidence but do not block a value.
- Keep SGX symbol normalization, SGD consistency, caching, provenance, and `/technical` separation unchanged.
- Keep normal tests fully mocked and preserve unrelated changes.

## File Map

- `app/services/bank_valuation.py`: pure historical normalization, book-equity projection, and residual-income scenarios.
- `app/services/valuation_router.py`: enable supported bank dispatch.
- `app/services/valuation_types.py`: carry optional bank-quality metrics.
- `app/schemas.py`: typed bank model details in the response union.
- `tests/test_bank_valuation.py`: pure model coverage.
- Existing facade, service, route, and API tests gain bank cases.

---

### Task 1: Pure residual-income engine

**Files:**
- Create: `app/services/bank_valuation.py`
- Create: `tests/test_bank_valuation.py`
- Modify: `app/services/valuation_types.py`

**Interfaces:**
- Consumes: `ValuationFundamentals` with common equity, net income common, common dividends, diluted shares, and at least three comparable annual periods.
- Produces: `normalize_bank_history(fundamentals: ValuationFundamentals) -> BankNormalizedInputs` and `value_bank(fundamentals: ValuationFundamentals) -> ModelResult`.

- [ ] **Step 1: Add optional typed bank-quality metrics**

Add to `ValuationFundamentals`:

```python
bank_metrics: dict[str, float] = Field(default_factory=dict)
```

Use only these keys in version 1: `cet1_ratio`, `npl_ratio`, `loan_loss_coverage`, and `regulatory_capital_headroom`.

- [ ] **Step 2: Write failing historical-normalization tests**

Create five annual bank periods with beginning/end common equity, net income, dividends, and shares. Assert:

```python
normalized = normalize_bank_history(fundamentals)
assert normalized.common_equity == 10_000.0
assert normalized.diluted_shares == 1_000.0
assert normalized.book_value_per_share == 10.0
assert normalized.normalized_roe == 0.12
assert normalized.payout_ratio == 0.40
```

Historical ROE must use `net_income / average(beginning_equity, ending_equity)`. Historical payout must use `abs(common_dividends) / positive_net_income`. Use the median of the last five valid observations. Add failure cases for negative equity, fewer than three observations, and payout below 0 or above 1.

- [ ] **Step 3: Run the tests to verify the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bank_valuation.py -q`

Expected: collection fails because `app.services.bank_valuation` does not exist.

- [ ] **Step 4: Implement normalized bank inputs**

Create an immutable dataclass:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class BankNormalizedInputs:
    common_equity: float
    diluted_shares: float
    normalized_roe: float
    payout_ratio: float
    book_value_per_share: float
    usable_years: int
```

Sort annual periods by period end. For each year after the first, use average prior/current positive common equity for ROE. Require positive net income to calculate payout. Validate all numeric values as finite.

- [ ] **Step 5: Write failing projection tests**

For a bank with common equity `10_000`, shares `1_000`, normalized ROE `12%`, and payout `40%`, assert each scenario:

- Starts at 90%, 100%, or 105% of normalized ROE.
- Fades linearly to its required return in year 10.
- Calculates net income from beginning equity.
- Calculates dividends as payout times net income.
- Calculates ending equity as beginning equity plus retained earnings.
- Discounts annual excess return at the scenario required return.
- Uses zero terminal excess return after year 10.

Assert `bear <= base <= bull`, values are finite and positive, and details expose normalized ROE, book value per share, payout ratio, usable years, and optional bank metrics.

- [ ] **Step 6: Implement residual-income scenarios**

Use these assumptions:

```python
scenario_inputs = {
    "bear": (0.90, 0.12),
    "base": (1.00, 0.10),
    "bull": (1.05, 0.08),
}
```

For years 1 through 10, interpolate ROE with `progress = (year - 1) / 9`, so year 1 uses `normalized_roe * factor` and year 10 uses `required_return`. Calculate and discount excess return before updating equity. Add discounted excess returns to current common equity, divide by diluted shares, and call `validate_scenarios`.

Return:

```python
ModelResult(
    method="bank_residual_income",
    detected_company_type="bank",
    bear=bear_value,
    base=base_value,
    bull=bull_value,
    details=details,
    assumptions=assumptions,
    quality=quality,
    warnings=warnings,
)
```

- [ ] **Step 7: Run pure bank tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bank_valuation.py tests\test_valuation_math.py -q`

Expected: all tests pass without provider access.

- [ ] **Step 8: Commit the bank engine**

```powershell
git add app/services/bank_valuation.py app/services/valuation_types.py tests/test_bank_valuation.py
git commit -m "Add bank residual income valuation"
```

---

### Task 2: Bank routing, response schema, and service integration

**Files:**
- Modify: `app/services/valuation_router.py`
- Modify: `app/services/valuation_service.py`
- Modify: `app/services/valuation_fundamentals.py`
- Modify: `app/schemas.py`
- Modify: `tests/test_valuation_router.py`
- Modify: `tests/test_valuation_service.py`
- Modify: `tests/test_valuation_fundamentals.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `value_bank(fundamentals)` from Task 1.
- Produces: supported bank classification, `BankValuationDetails`, and public `bank_residual_income` responses.

- [ ] **Step 1: Write failing supported-bank routing tests**

Update the bank fixture to include compatible common-equity statements. Assert `classify_company` returns `company_type="bank"`, `supported=True`, and at least two classification sources. Assert `route_valuation` calls `value_bank` once and never calls `value_owner_earnings`.

Add conflicting metadata tests that still return `valuation_unreliable` rather than forcing bank routing.

- [ ] **Step 2: Enable bank dispatch**

Import `value_bank` and replace the recognized-but-unsupported bank branch with:

```python
if classification.company_type == "bank":
    return value_bank(fundamentals)
```

Do not change REIT or insurer behavior. Increment `VALUATION_MODEL_VERSION` in `valuation_service.py` from `"1"` to `"2"` so cached foundation results cannot survive the new bank model.

- [ ] **Step 3: Add typed bank response details**

Add `BankValuationDetails` to `app/schemas.py` with:

```python
method: Literal["bank_residual_income"]
normalized_roe: float
book_value_per_share: float
payout_ratio: float
usable_years: int
projected_book_equity: dict[str, list[float]]
cet1_ratio: float | None = None
npl_ratio: float | None = None
loan_loss_coverage: float | None = None
```

Expand the model-details union to accept owner-earnings or bank details. Keep the discriminator field named `method`.

- [ ] **Step 4: Normalize optional bank metrics at the facade**

Map same-currency provider fields into the four approved `bank_metrics` keys. Record each source independently. Never infer a missing CET1 or NPL ratio from unrelated balance-sheet rows.

- [ ] **Step 5: Write service and API bank tests**

Assert `get_valuation("SGX", "D05")` and `get_valuation("SGX", "D05.SI")` return public symbol `SGX:D05`, currency `SGD`, `method="bank_residual_income"`, finite ordered scenarios, bank details, field sources, and no owner-earnings fields.

In `tests/test_api.py`, assert both URL forms return the same normalized response and `/technical` remains independent.

- [ ] **Step 6: Run bank integration tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bank_valuation.py tests\test_valuation_fundamentals.py tests\test_valuation_router.py tests\test_valuation_service.py tests\test_api.py -q`

Expected: all bank, service, and route tests pass.

- [ ] **Step 7: Commit bank integration**

```powershell
git add app/services/valuation_router.py app/services/valuation_service.py app/services/valuation_fundamentals.py app/schemas.py tests/test_valuation_router.py tests/test_valuation_service.py tests/test_valuation_fundamentals.py tests/test_api.py
git commit -m "Route bank intrinsic valuations"
```

---

### Task 3: Bank documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `API_DOCUMENTATION.md`
- Modify: `tests/test_live_valuation.py`

**Interfaces:**
- Consumes: completed bank behavior.
- Produces: documented and optionally live-verified SGX bank valuation.

- [ ] **Step 1: Document bank behavior**

Document residual-income formula, required inputs, scenario returns, ROE fade, zero terminal excess return, optional quality metrics, confidence behavior, and `SGX/D05/valuation` example. State that bank debt and working capital are not processed as ordinary-company owner earnings.

- [ ] **Step 2: Add optional SGX bank live smoke coverage**

Extend the live valuation test gate with one SGX bank. Assert method, SGD, schema, finite ordered scenarios, and source metadata only; do not assert a hard-coded intrinsic value.

- [ ] **Step 3: Run focused and full tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_bank_valuation.py tests\test_valuation_router.py tests\test_valuation_service.py tests\test_api.py -q`

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: focused and complete mocked suites pass; live tests remain skipped by default.

- [ ] **Step 4: Run cleanup verification**

Run: `git diff --check`

Run: `rg -n "bank_residual_income|normalized_roe|book_value_per_share|D05" app tests README.md API_DOCUMENTATION.md`

Expected: no whitespace errors and bank contract coverage exists across code, tests, and docs.

- [ ] **Step 5: Commit bank documentation**

```powershell
git add README.md API_DOCUMENTATION.md tests/test_live_valuation.py
git commit -m "Document bank intrinsic valuation"
```

## Plan Completion Gate

Do not start the REIT plan until:

- Mocked `SGX/D05` and `SGX/D05.SI` both return bank residual-income values in SGD.
- Owner-earnings code is never called for a bank.
- Invalid equity, payout, units, and scenario ordering return deterministic errors or `valuation_unreliable` as specified.
- Focused tests, the full mocked suite, and `git diff --check` pass.
