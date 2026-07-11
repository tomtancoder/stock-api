# SEC YTD TTM Owner Earnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recent SEC-backed TTM period from annual and matching year-to-date facts, so eligible US operating companies can receive an owner-earnings DCF without lowering the quality threshold.

**Architecture:** Keep SEC normalization in `app/services/sec_companyfacts.py`. Add a cumulative annual/YTD TTM strategy beside the existing standalone-quarter strategy. The provider returns the newest complete period; the existing owner-earnings engine consumes it unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, mocked SEC Company Facts payloads.

## Global Constraints

- Do not change public routes, schemas, company classification, DCF assumptions, or SGX routing.
- Derive each additive field only as `prior FY - prior-year matching YTD + current-year matching YTD`.
- Use the same SEC concept and unit for all three facts of a derived field.
- Require matching Q1, Q2, or Q3 SEC calendar frames one year apart; SEC `fy`
  metadata can describe the filing year for comparative facts and is not used
  as the period identity.
- Preserve current-year SEC provenance and retain the standalone-quarter strategy.
- Keep the current two-independent-components owner-earnings requirement.

---

## File Structure

- Modify: `app/services/sec_companyfacts.py` — annual/YTD window selection and TTM construction.
- Modify: `tests/test_sec_companyfacts.py` — SEC provider accuracy and incomplete-data safety tests.

### Task 1: Define the failing provider and model contracts

**Files:**
- Modify: `tests/test_sec_companyfacts.py`

**Interfaces:**
- Consumes: `sec_fact()`, `company_facts()`, `install_fetch_payloads()`, and `fetch_sec_fundamentals()`.
- Produces: a `FinancialPeriod(is_ttm=True)` ending on the current YTD period.

- [ ] **Step 1: Add the provider regression test**

Create `test_derives_latest_ttm_from_annual_and_matching_ytd_facts`. For each of operating cash flow, capital expenditure, stock compensation, and revenue, provide one 2025 FY fact, one `CY2025Q1` YTD fact, and one `CY2026Q1` YTD fact using one concept and `USD`. Use these operating-cash-flow values:

```python
annual_2025 = sec_fact(14_747.0, start="2025-01-01", end="2025-12-31", fiscal_year=2025, fiscal_period="FY", frame="CY2025")
prior_q1 = sec_fact(2_156.0, start="2025-01-01", end="2025-03-31", form="10-Q", filed="2025-04-30", accession="prior-q1", fiscal_year=2025, fiscal_period="Q1", frame="CY2025Q1")
current_q1 = sec_fact(3_937.0, start="2026-01-01", end="2026-03-31", form="10-Q", filed="2026-04-30", accession="current-q1", fiscal_year=2026, fiscal_period="Q1", frame="CY2026Q1")
```

Assert:

```python
assert ttm.period_end.isoformat() == "2026-03-31"
assert ttm.operating_cash_flow == pytest.approx(16_528.0)
assert ttm.capital_expenditure == pytest.approx(9_528.0)
assert ttm.stock_based_compensation == pytest.approx(3_282.0)
assert ttm.revenue == pytest.approx(97_879.0)
assert ttm.sources["operating_cash_flow"].accession == "current-q1"
```

Include at least three positive annual owner-earnings years and import `value_owner_earnings` from `app.services.owner_earnings_valuation`. Assert that the provider output can be valued:

```python
model_result = value_owner_earnings(fundamentals)
assert model_result.method == "owner_earnings_dcf"
assert model_result.quality["eligible"] is True
```

- [ ] **Step 2: Run the provider test to verify red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py::test_derives_latest_ttm_from_annual_and_matching_ytd_facts -q
```

Expected: FAIL because the existing implementation has no recent cumulative TTM path, leaving owner-earnings normalization with only one independent component.

- [ ] **Step 3: Add the incomplete-comparison safety test**

Create `test_does_not_derive_ytd_ttm_without_prior_matching_quarter`. Use a 2025 annual fact plus `CY2026Q1`, but omit `CY2025Q1`. Assert no TTM ends on `2026-03-31`.

- [ ] **Step 4: Run the safety test before implementation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py::test_does_not_derive_ytd_ttm_without_prior_matching_quarter -q
```

Expected: PASS; this proves the test blocks unrelated annual/current-quarter mixing.

- [ ] **Step 5: Commit the red contracts**

```powershell
git add tests\test_sec_companyfacts.py
git commit -m "Test SEC annual YTD TTM valuation"
```

### Task 2: Implement cumulative SEC TTM at the provider boundary

**Files:**
- Modify: `app/services/sec_companyfacts.py:151-157`
- Modify: `app/services/sec_companyfacts.py:365-487`

**Interfaces:**
- Consumes: `_SecFact`, `_CONCEPTS`, `_ADDITIVE_FIELDS`, `_INSTANT_FIELDS`, `_is_annual_fact()`, `_provenance()`, and the standalone-quarter builder.
- Produces: `_build_annual_ytd_ttm_period(concepts, currency) -> FinancialPeriod | None`.

- [ ] **Step 1: Add the window type**

Add after `_TtmWindow`:

```python
@dataclass(frozen=True)
class _AnnualYtdTtmWindow:
    annual: _SecFact
    prior_ytd: _SecFact
    current_ytd: _SecFact
    field_priority: int
    concept_priority: int
```

- [ ] **Step 2: Add `_latest_annual_ytd_ttm_window()`**

For every non-instant field and each concept independently, select a current fact whose frame is Q1-Q3. Find the prior fact at `(_frame_index(current.frame) - 4)` and an annual fact ending between the prior YTD end and the current YTD start. Require:

```python
_is_annual_fact(annual, field)
current.start is not None and prior_ytd.start is not None
prior_ytd.end < annual.end <= current.start
0 < (current.end - current.start).days <= 300
0 < (prior_ytd.end - prior_ytd.start).days <= 300
```

Return the candidate with the latest `current_ytd.end`, breaking ties by field then concept priority.

- [ ] **Step 3: Add `_select_annual_ytd_facts()`**

Given one field and an annual/YTD window, return an `(annual, prior_ytd, current_ytd)` triple from exactly one concept. Return `None` when no compatible triple exists. Compute each additive field as:

```python
derived_value = annual.value - prior_ytd.value + current_ytd.value
```

Use `current_ytd` for provenance.

- [ ] **Step 4: Add `_build_annual_ytd_ttm_period()`**

Populate additive fields with `_select_annual_ytd_facts()`. Populate instant fields with `_select_instant_at_window_end()` and diluted shares with a compatible duration fact ending at `current_ytd.end`. Set `interest_paid_outside_operating` to `0.0` for all SEC-derived periods because US GAAP includes cash interest in operating cash flow. Return `None` if the candidate has no fields.

- [ ] **Step 5: Choose the newest strategy**

Rename the current implementation to `_build_standalone_quarter_ttm_period()`. Have `_build_ttm_period()` compare it with `_build_annual_ytd_ttm_period()` by `period_end`; on equal dates retain the standalone result because it is directly reported.

- [ ] **Step 6: Run focused green tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py tests\test_owner_earnings_valuation.py -q
```

Expected: PASS, including all existing standalone-quarter and sparse-data tests.

- [ ] **Step 7: Commit the provider change**

```powershell
git add app\services\sec_companyfacts.py tests\test_sec_companyfacts.py
git commit -m "Derive SEC TTM from annual YTD facts"
```

### Task 3: Verify the public valuation contract

**Files:**
- Modify: no production or test files; this task validates the completed provider change.

**Interfaces:**
- Consumes: `GET /api/v1/markets/NASDAQ/TSLA/valuation`.
- Produces: evidence that the public endpoint retains strict behavior while consuming SEC-derived TTM facts.

- [ ] **Step 1: Run suite and static verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
python -m compileall app
git diff --check
```

Expected: all tests pass, compilation succeeds, and the diff check has no output.

- [ ] **Step 2: Restart one local API instance and verify Tesla**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/markets/NASDAQ/TSLA/valuation" |
  Select-Object status, method, intrinsic_value, confidence, data_quality, warnings |
  ConvertTo-Json -Depth 6
```

Expected: `primary_source` is `sec_companyfacts`; a complete SEC fact set returns `status: ok` and `method: owner_earnings_dcf`. Missing required facts must remain explicitly `valuation_unreliable`.

- [ ] **Step 3: Confirm the working tree is clean after the Task 2 commit**

```powershell
git status --short
```

Expected: no output. The implementation and test commit is created in Task 2, Step 7.

## Self-Review

| Requirement | Coverage |
| --- | --- |
| Annual/YTD TTM formula | Task 1 and Task 2 |
| Same concept and unit | Task 2, Steps 2-4 |
| Reject incomplete comparisons | Task 1, Steps 3-4 |
| Preserve standalone-quarter behavior | Task 2, Step 5 |
| Keep valuation quality strict | Tasks 1 and 3 |
| Live Tesla confirmation | Task 3, Step 2 |

The plan has no deferred work markers and does not broaden the API surface.
