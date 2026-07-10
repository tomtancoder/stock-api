# REIT Valuation and Final Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SGX and US REIT distribution/NAV valuation, enable the final model router, and verify the complete ordinary-company, bank, and REIT endpoint contract.

**Architecture:** Extend normalized provider facts with DPU, units, NAV, and optional property-quality metrics, then add a pure ten-year distribution-plus-terminal-NAV engine with a distribution-only fallback. Enable REIT routing through the existing service and finish cross-model docs, API tests, caching tests, and opt-in live smoke coverage.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pandas, yfinance 1.5.1+, pytest

## Global Constraints

- Complete the foundation/owner-earnings and bank plans first.
- Use distributions and NAV for REITs; never run owner-earnings DCF or bank residual income for a detected REIT.
- Require at least three reliable distribution years.
- Prefer issuer-reported DPU and NAV; otherwise derive DPU from distributions and NAV per unit from compatible unitholder equity and units.
- Keep AFFO and FFO supporting-only because AFFO definitions vary by issuer.
- Return PV of ten years of DPU and PV of terminal NAV separately.
- Use the approved scenario returns, growth bounds, terminal NAV factors, and 25% margin of safety.
- Permit `reit_distribution_only` only when DPU history is adequate and NAV is missing; mark confidence low.
- Preserve SGX `.SI` normalization, SGD consistency, daily valuation caching, quote refresh, provenance, and `/technical` separation.
- Finish with finite positive `bear <= base <= bull` validation across all three models.
- Keep normal tests fully mocked and preserve unrelated changes.

## File Map

- `app/services/reit_valuation.py`: pure DPU normalization, NAV projection, distribution discounting, and fallback.
- `app/services/yfinance_statements.py`: normalize dividend history, units, NAV, and optional REIT metrics.
- `app/services/sec_companyfacts.py`: normalize US REIT distributions and equity/unit facts when present.
- `app/services/valuation_fundamentals.py`: derive compatible DPU/NAV facts and provenance.
- `app/services/valuation_router.py`: enable supported REIT dispatch.
- `app/schemas.py`: typed REIT model details in the final response union.
- Focused tests cover provider normalization, pure math, routing, service, route, caching, and final regressions.

---

### Task 1: Normalize REIT distributions, units, NAV, and quality facts

**Files:**
- Modify: `app/services/valuation_types.py`
- Modify: `app/services/yfinance_statements.py`
- Modify: `app/services/sec_companyfacts.py`
- Modify: `app/services/valuation_fundamentals.py`
- Modify: `tests/test_yfinance_statements.py`
- Modify: `tests/test_sec_companyfacts.py`
- Modify: `tests/test_valuation_fundamentals.py`

**Interfaces:**
- Consumes: provider dividend history, unitholder/common equity, units outstanding, total assets/debt, and optional issuer metrics.
- Produces: annual `distribution_per_unit`, current `nav_per_unit`, and approved `reit_metrics` on `ValuationFundamentals`.

- [ ] **Step 1: Add typed REIT quality metrics**

Add to `ValuationFundamentals`:

```python
reit_metrics: dict[str, float] = Field(default_factory=dict)
```

Use only these keys in version 1: `aggregate_leverage`, `interest_coverage`, `occupancy`, `wale_years`, `recurring_property_capex`, and `material_currency_exposure`.

- [ ] **Step 2: Write failing SGX distribution and NAV tests**

Use a fake `.SI` REIT ticker with monthly/quarterly dividend history, three annual balance sheets, and unit history. Assert:

- Dividends are grouped into annual DPU without multiplying by units.
- Trailing DPU covers only the most recent twelve months.
- NAV per unit equals compatible unitholder equity divided by units outstanding.
- SGD is preserved across price, distributions, equity, and units.
- Missing NAV stays `None` rather than zero.
- Provider row names and dividend source are retained in field provenance.

- [ ] **Step 3: Implement yFinance REIT normalization**

When provider type or industry indicates a REIT/property trust, read `Ticker.dividends` and add distribution periods without overwriting issuer-reported DPU. Align derived annual distributions to the issuer fiscal year-end when metadata provides it; otherwise group by calendar year and add a warning. Use the most recent compatible units for each period. Derive NAV per unit only when positive equity and units share the same period and currency.

Normalize optional leverage from a provider-reported aggregate leverage field when present. If only total debt and total assets exist, store `derived_aggregate_leverage` in sources and expose the calculated value under `aggregate_leverage` with a warning that it is derived.

- [ ] **Step 4: Write failing SEC REIT fact tests**

Add Company Facts fixtures for distributions to common/unit holders, weighted units, common/unitholder equity, real-estate depreciation, and gains on property sales. Assert unit/currency separation, latest-amendment selection, and provenance. Do not require FFO/AFFO for eligibility.

- [ ] **Step 5: Extend SEC and facade normalization**

Add ordered concept aliases for REIT-compatible dividends/distributions, units, and equity. At the facade, prefer issuer/provider DPU and NAV; derive only from compatible facts. Merge optional REIT metrics without overwriting primary facts and cap confidence according to source quality.

- [ ] **Step 6: Run provider/facade tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_yfinance_statements.py tests\test_sec_companyfacts.py tests\test_valuation_fundamentals.py -q`

Expected: all existing and new provider tests pass with no live calls.

- [ ] **Step 7: Commit REIT fundamental normalization**

```powershell
git add app/services/valuation_types.py app/services/yfinance_statements.py app/services/sec_companyfacts.py app/services/valuation_fundamentals.py tests/test_yfinance_statements.py tests/test_sec_companyfacts.py tests/test_valuation_fundamentals.py
git commit -m "Normalize REIT valuation fundamentals"
```

---

### Task 2: Pure distribution and NAV valuation engine

**Files:**
- Create: `app/services/reit_valuation.py`
- Create: `tests/test_reit_valuation.py`

**Interfaces:**
- Consumes: `ValuationFundamentals` with at least three annual DPU values, current units, and optional NAV history.
- Produces: `normalize_reit_history(fundamentals: ValuationFundamentals) -> ReitNormalizedInputs` and `value_reit(fundamentals: ValuationFundamentals) -> ModelResult`.

- [ ] **Step 1: Write failing DPU normalization tests**

Create annual fixtures and assert normalization takes the median of trailing DPU, weighted three-year DPU with weights `1, 2, 3`, and five-year median DPU. Require at least three usable years and at least two independent normalization components.

Assert DPU CAGR is clamped to `[-0.03, 0.03]` and NAV-per-unit CAGR is clamped to `[-0.02, 0.025]`.

- [ ] **Step 2: Run tests to verify the red phase**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_reit_valuation.py -q`

Expected: collection fails because `app.services.reit_valuation` does not exist.

- [ ] **Step 3: Implement normalized REIT inputs**

Create:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ReitNormalizedInputs:
    normalized_dpu: float
    current_nav_per_unit: float | None
    base_dpu_growth: float
    base_nav_growth: float
    usable_years: int
```

Use positive finite DPU observations. A missing NAV is permitted only for the distribution-only fallback. Preserve negative historical growth inside the approved clamps.

- [ ] **Step 4: Write failing scenario tests**

For normalized DPU `0.06` and NAV per unit `1.10`, assert:

- Bear DPU growth is `max(-0.05, base_growth - 0.02)`.
- Base DPU growth stays in `[-0.03, 0.03]`.
- Bull DPU growth is `min(0.05, base_growth + 0.02)`.
- Bear/base/bull returns are `10%`, `8.5%`, and `7%`.
- Starting DPU factors are `90%`, `100%`, and `105%`.
- NAV growth uses base minus/plus one percentage point inside `[-0.02, 0.025]`.
- Terminal NAV factors are `85%`, `100%`, and `110%`.
- Details separate PV of distributions from PV of terminal NAV.
- Values satisfy `bear <= base <= bull`.

- [ ] **Step 5: Implement ten-year DPU plus terminal NAV valuation**

For each scenario, project and discount ten annual distributions at constant scenario DPU growth. Project NAV per unit for ten years at scenario NAV growth, multiply by the terminal NAV factor, discount it from year ten, and add both present values.

Return `method="reit_distribution_nav"` and expose normalized DPU, current NAV per unit, price-to-NAV input, distribution yield input, usable years, optional quality metrics, and scenario contribution breakdown.

- [ ] **Step 6: Add and implement distribution-only fallback tests**

When NAV is absent and DPU history is adequate, calculate ten years of distributions plus Gordon terminal value `DPU10 * (1 + terminal_growth) / (required_return - terminal_growth)` using terminal growth `0%`, `1.5%`, and `2.5%` for bear/base/bull. Require return greater than terminal growth and discount the terminal value from year 10. Return `method="reit_distribution_only"`, add a warning, and set a quality flag that forces low confidence.

When DPU history is insufficient, raise `ValuationUnreliable` with reasons.

- [ ] **Step 7: Run pure REIT tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_reit_valuation.py tests\test_valuation_math.py -q`

Expected: all REIT and common-math tests pass.

- [ ] **Step 8: Commit the REIT engine**

```powershell
git add app/services/reit_valuation.py tests/test_reit_valuation.py
git commit -m "Add REIT distribution and NAV valuation"
```

---

### Task 3: Enable REIT routing and typed public responses

**Files:**
- Modify: `app/services/valuation_router.py`
- Modify: `app/services/valuation_service.py`
- Modify: `app/schemas.py`
- Modify: `tests/test_valuation_router.py`
- Modify: `tests/test_valuation_service.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `value_reit(fundamentals)` from Task 2.
- Produces: supported REIT routing and typed `reit_distribution_nav` or `reit_distribution_only` responses.

- [ ] **Step 1: Write failing supported-REIT router tests**

Use explicit REIT type/industry plus compatible distribution facts. Assert classification returns `company_type="reit"`, `supported=True`, and takes precedence over broad financial or real-estate labels. Assert dispatch calls `value_reit` exactly once and never calls bank or owner engines.

- [ ] **Step 2: Enable REIT dispatch**

Import `value_reit` and add:

```python
if classification.company_type == "reit":
    return value_reit(fundamentals)
```

Keep ambiguous classifications unreliable and insurers unsupported. Increment `VALUATION_MODEL_VERSION` in `valuation_service.py` from `"2"` to `"3"` so cached pre-REIT results cannot survive the new router behavior.

- [ ] **Step 3: Add typed REIT response details**

Add `ReitValuationDetails` with:

```python
method: Literal["reit_distribution_nav", "reit_distribution_only"]
normalized_dpu: float
nav_per_unit: float | None
price_to_nav: float | None
distribution_yield: float
usable_years: int
present_value_distributions: dict[str, float]
present_value_terminal: dict[str, float]
aggregate_leverage: float | None = None
interest_coverage: float | None = None
occupancy: float | None = None
wale_years: float | None = None
```

Expand the final model-details union to owner earnings, bank, or REIT details using `method` as discriminator.

- [ ] **Step 4: Write service and API REIT tests**

Assert an SGX REIT fixture returns public `.SI`-free symbol, SGD, selected REIT method, finite ordered scenarios, model details, provenance, and medium confidence when NAV is present. Assert missing NAV produces distribution-only method and low confidence. Assert incomplete DPU returns `valuation_unreliable` without another model fallback.

- [ ] **Step 5: Run routing/service/API tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_reit_valuation.py tests\test_valuation_router.py tests\test_valuation_service.py tests\test_api.py -q`

Expected: all REIT integration tests and prior ordinary/bank route tests pass.

- [ ] **Step 6: Commit REIT integration**

```powershell
git add app/services/valuation_router.py app/services/valuation_service.py app/schemas.py tests/test_valuation_router.py tests/test_valuation_service.py tests/test_api.py
git commit -m "Route REIT intrinsic valuations"
```

---

### Task 4: Final cross-model documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `API_DOCUMENTATION.md`
- Modify: `tests/test_live_valuation.py`

**Interfaces:**
- Consumes: completed ordinary-company, bank, and REIT implementation.
- Produces: final documented contract and complete regression evidence.

- [ ] **Step 1: Complete public documentation**

Document automatic model routing, REIT DPU/NAV formula, scenario assumptions, distribution-only fallback, confidence, provenance, daily versus quote refresh, SGX symbol forms, unsupported insurers/funds, and the strict separation from `/technical`.

Include one request/response example for an ordinary US company, an ordinary SGX company, an SGX bank, and an SGX REIT. Use illustrative values and label them as examples.

- [ ] **Step 2: Add optional SGX REIT live smoke coverage**

Extend `tests/test_live_valuation.py` with one SGX REIT under `RUN_LIVE_VALUATION_TESTS=1`. Assert only public symbol, SGD, accepted REIT method, schema, sources, finite values, and scenario ordering. Permit `valuation_unreliable` only when the response contains explicit missing-field reasons.

- [ ] **Step 3: Run every focused valuation test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_market_symbols.py tests\test_valuation_math.py tests\test_owner_earnings_valuation.py tests\test_bank_valuation.py tests\test_reit_valuation.py tests\test_yfinance_statements.py tests\test_sec_companyfacts.py tests\test_valuation_fundamentals.py tests\test_valuation_router.py tests\test_valuation_service.py tests\test_api.py -q`

Expected: all focused tests pass without network access.

- [ ] **Step 4: Run the complete mocked suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all normal tests pass and all live tests remain skipped by default.

- [ ] **Step 5: Verify public invariants and stale terms**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n "owner_earnings_dcf|bank_residual_income|reit_distribution_nav|reit_distribution_only|valuation_unreliable" app tests README.md API_DOCUMENTATION.md`

Expected: every method and failure status appears in its engine, route/service tests, and documentation.

Run: `rg -n "POST /api/v1/stocks/.*/valuation|Valuation is not supported" README.md API_DOCUMENTATION.md app tests`

Expected: the legacy stock route remains documented as retired/`501`; no documentation presents it as the new intrinsic-value endpoint.

- [ ] **Step 6: Review the complete scoped diff**

Run: `git status --short`

Run: `git diff --stat`

Expected: only valuation, shared symbol, configuration, tests, and documentation files named across the three plans are present; unrelated user files remain untouched.

- [ ] **Step 7: Commit final documentation and live coverage**

```powershell
git add README.md API_DOCUMENTATION.md tests/test_live_valuation.py
git commit -m "Complete multi-model valuation documentation"
```

## Final Completion Gate

The feature is complete only when:

- Ordinary operating companies, banks, and REITs select only their compatible model.
- Mocked US and SGX fixtures cover every supported model.
- SGX symbol, SGD, and source-provenance behavior is consistent.
- The intrinsic valuation cache and quote refresh remain independent.
- Invalid, missing, stale, ambiguous, and unsupported cases match the API contract.
- `/technical`, `/analysis`, `/quote`, backtests, sentiment, and news regressions remain green.
- The complete mocked suite and `git diff --check` pass.
