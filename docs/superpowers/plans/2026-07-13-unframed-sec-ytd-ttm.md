# Unframed SEC YTD TTM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a current SEC-backed intrinsic-value range for AAPL when its valid 10-Q year-to-date facts lack `CY...Q...` frame metadata.

**Architecture:** `app/services/sec_companyfacts.py` remains the only provider-normalization seam. Annual records use their true period-end year, and the annual/YTD TTM builder accepts coherent framed or unframed fiscal YTD pairs. The existing owner-earnings model and public route consume the resulting complete TTM period unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, SEC Company Facts.

## Global Constraints

- Do not change routes, schemas, DCF assumptions, company classification, or yFinance fallback policy.
- Keep the owner-earnings quality gate strict; incomplete facts must remain ineligible.
- Derive additive facts only as `prior FY - prior YTD + current YTD`, using one concept and currency per field.
- An unframed pair must be 10-Q duration facts, share a fiscal period when supplied, have 75-300 day durations, and have starts and ends 300-430 days apart.
- Emit annual/YTD TTM only when operating cash flow, capital expenditure, stock-based compensation, and revenue are present.
- Preserve standalone-quarter TTM behavior and select the newest complete TTM.

---

## File Structure

- Modify: `app/services/sec_companyfacts.py` — annual identity and unframed annual/YTD selection.
- Modify: `tests/test_sec_companyfacts.py` — provider regressions.
- Validate: `tests/test_live_valuation.py` — AAPL returns a non-null intrinsic range.

### Task 1: Preserve historical annual identity

**Files:**

- Modify: `tests/test_sec_companyfacts.py`
- Modify: `app/services/sec_companyfacts.py:338-378`

**Interfaces:**

- Consumes: `_build_annual_periods(concepts, currency)`.
- Produces: annual `FinancialPeriod` values where `fiscal_year == period_end.year`.

- [ ] **Step 1: Write the failing comparative-filing test**

```python
def test_annual_periods_keep_historical_years_when_latest_filing_reports_comparatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = company_facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "USD": [
                    sec_fact(
                        year * 10,
                        start=f"{year}-01-01",
                        end=f"{year}-12-31",
                        filed="2026-02-01",
                        accession="annual-2025",
                        fiscal_year=2025,
                        frame=f"CY{year}",
                    )
                    for year in range(2021, 2026)
                ]
            }
        }
    )
    install_fetch_payloads(monkeypatch, facts)

    result = sec_companyfacts.fetch_sec_fundamentals("NASDAQ", "AAPL")

    assert [
        period.fiscal_year for period in result.periods if not period.is_ttm
    ] == [2021, 2022, 2023, 2024, 2025]
```

- [ ] **Step 2: Verify red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py::test_annual_periods_keep_historical_years_when_latest_filing_reports_comparatives -q
```

Expected: every period is incorrectly labelled `2025`.

- [ ] **Step 3: Implement the source-level identity rule**

Replace fiscal-year accumulation after field selection with:

```python
        if not sources:
            continue
        values["fiscal_year"] = period_end.year
        values["sources"] = sources
        periods.append(FinancialPeriod(**values))
```

Remove the local `fiscal_years` list and its append operation. Keep the selected values and provenance unchanged.

- [ ] **Step 4: Verify green and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py -q
git add app\services\sec_companyfacts.py tests\test_sec_companyfacts.py
git commit -m "Preserve SEC annual period identity"
```

Expected: SEC provider suite passes.

### Task 2: Derive TTM from unframed fiscal YTD facts

**Files:**

- Modify: `tests/test_sec_companyfacts.py`
- Modify: `app/services/sec_companyfacts.py:514-606,860-867`

**Interfaces:**

- Consumes: `_SecFact`, `_AnnualYtdTtmWindow`, `_CONCEPTS`, and `_latest_compatible_duration_fact()`.
- Produces: `_latest_annual_ytd_ttm_window(all_facts) -> _AnnualYtdTtmWindow | None` for framed and unframed 10-Q facts.

- [ ] **Step 1: Write the failing Apple-like unframed fixture**

Add `test_derives_ttm_from_unframed_matching_fiscal_ytd_facts`. Build 2025 annual facts plus prior/current Q2 YTD facts with these boundaries and no frame:

```python
prior = sec_fact(
    prior_value,
    start="2024-09-29",
    end="2025-03-29",
    form="10-Q",
    filed="2025-05-02",
    accession="prior-q2",
    fiscal_year=2025,
    fiscal_period="Q2",
    frame=None,
)
current = sec_fact(
    current_value,
    start="2025-09-28",
    end="2026-03-28",
    form="10-Q",
    filed="2026-05-01",
    accession="current-q2",
    fiscal_year=2026,
    fiscal_period="Q2",
    frame=None,
)
```

Use this fixture for operating cash flow, capital expenditure, stock-based compensation, and revenue. Assert the TTM ends `2026-03-28`, each additive value equals `annual - prior + current`, and the current operating-cash-flow accession is `current-q2`. Include three positive annual owner-earnings periods, then assert:

```python
model_result = value_owner_earnings(fundamentals)
assert model_result.method == "owner_earnings_dcf"
assert model_result.quality["eligible"] is True
```

- [ ] **Step 2: Verify red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py::test_derives_ttm_from_unframed_matching_fiscal_ytd_facts -q
```

Expected: no TTM ending `2026-03-28` is emitted.

- [ ] **Step 3: Add duration and pair predicates**

Add these helpers beside `_is_ytd_fact()`:

```python
def _is_ytd_duration_fact(fact: _SecFact) -> bool:
    if fact.form not in {"10-Q", "10-Q/A"} or fact.start is None:
        return False
    return 75 <= (fact.end - fact.start).days <= 300


def _matching_ytd_periods(prior: _SecFact, current: _SecFact) -> bool:
    if prior.start is None or current.start is None:
        return False
    if (
        prior.fiscal_period is not None
        and current.fiscal_period is not None
        and prior.fiscal_period != current.fiscal_period
    ):
        return False
    return (
        300 <= (current.start - prior.start).days <= 430
        and 300 <= (current.end - prior.end).days <= 430
        and abs(
            (current.end - current.start).days
            - (prior.end - prior.start).days
        ) <= 31
    )
```

- [ ] **Step 4: Implement date-based window selection**

In `_latest_annual_ytd_ttm_window()`, deduplicate `_is_ytd_duration_fact()` observations by `(start, end)` with `_latest_compatible_duration_fact()`. Test each earlier/later pair with `_matching_ytd_periods()`. Retain a pair only if an annual fact satisfies:

```python
prior_ytd.end < annual.end <= current_ytd.start
and _is_annual_fact(annual, field)
```

Return the candidate with the latest `current_ytd.end`, then existing field and concept priorities. Framed facts meet the same date constraints, preserving existing behavior.

- [ ] **Step 5: Select every field by exact periods**

In `_select_annual_ytd_facts()`, replace frame checks with exact start/end checks:

```python
prior_candidates = [
    fact for fact in concepts_by_name[concept]
    if fact.start == window.prior_ytd.start
    and fact.end == window.prior_ytd.end
    and _is_ytd_duration_fact(fact)
]
current_candidates = [
    fact for fact in concepts_by_name[concept]
    if fact.start == window.current_ytd.start
    and fact.end == window.current_ytd.end
    and _is_ytd_duration_fact(fact)
]
```

Keep annual selection, same-concept enforcement, current-period provenance, and the complete-owner-earnings-field guard intact.

- [ ] **Step 6: Verify green and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_companyfacts.py tests\test_owner_earnings_valuation.py -q
git add app\services\sec_companyfacts.py tests\test_sec_companyfacts.py
git commit -m "Match unframed SEC YTD facts"
```

Expected: framed, unframed, standalone, and incomplete-window tests pass.

### Task 3: Prove the AAPL public response contains an intrinsic value

**Files:**

- Validate: `tests/test_live_valuation.py`

**Interfaces:**

- Consumes: `GET /api/v1/markets/NASDAQ/AAPL/valuation` through `TestClient`.
- Produces: non-null, positive, ordered bear/base/bull intrinsic values.

- [ ] **Step 1: Run the opt-in live AAPL assertion**

```powershell
$env:RUN_LIVE_VALUATION_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest 'tests\test_live_valuation.py::test_live_ordinary_company_valuation[us-aapl]' --override-ini addopts='' -q
```

Expected: `1 passed`; the existing test checks `intrinsic_value` is non-null and all scenarios are finite, positive, and ordered.

- [ ] **Step 2: Run the standard suite and hygiene checks**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app
git diff --check
git status --short
```

Expected: normal tests pass, compilation succeeds, diff check has no errors, and only intended implementation changes remain.

- [ ] **Step 3: Commit any uncommitted implementation files**

```powershell
git add app\services\sec_companyfacts.py tests\test_sec_companyfacts.py
git commit -m "Restore AAPL SEC intrinsic valuation"
```

## Self-Review

| Requirement | Coverage |
| --- | --- |
| Distinct historical annuals | Task 1 |
| Unframed fiscal-YTD matching | Task 2 |
| Strict complete-field guard | Global Constraints and Task 2 |
| Existing TTM behavior | Task 2 Step 6 |
| AAPL intrinsic value | Task 3 Step 1 |

The plan adds no routes, schemas, valuation assumptions, or fallback-provider behavior.
