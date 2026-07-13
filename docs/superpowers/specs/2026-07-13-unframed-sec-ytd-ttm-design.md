# Unframed SEC YTD TTM design

## Goal

Restore owner-earnings valuations for US issuers such as AAPL when their valid
10-Q cash-flow facts are cumulative fiscal-year-to-date values without SEC
`CY...Q...` frame metadata.

## Root cause

The SEC provider currently uses the `fy` field of the latest filing as the
identity of an annual period. Comparative facts in a later filing therefore
label several historical annual periods with the same fiscal year and collapse
the owner-earnings history.

The annual/YTD TTM builder also accepts only YTD facts with a calendar-quarter
frame. Apple's 10-Q operating cash flow and capital-expenditure facts have
valid start and end dates, form, and fiscal-quarter metadata, but no frame.
The provider consequently selects an obsolete standalone TTM instead of the
newest complete annual/YTD window.

## Design

Annual SEC `FinancialPeriod` objects use `period_end.year` as their fiscal-year
identity. This preserves the latest revised value for each historical period
while preventing comparative facts from merging unrelated annual periods.

The annual/YTD TTM path keeps its existing framed matching behavior and adds a
date-based path for unframed 10-Q facts. An unframed match requires:

- prior and current facts are 10-Q duration facts of 75 to 300 days;
- both report the same fiscal quarter when that metadata exists;
- their starts and ends are approximately one fiscal year apart;
- exactly one compatible 10-K annual fact ends after the prior YTD period and
  on or before the current YTD period starts; and
- every derived owner-earnings field uses one concept and a matching
  annual/prior-YTD/current-YTD triple.

The derived value remains:

```
TTM = prior FY - prior-year matching YTD + current-year matching YTD
```

The provider emits no annual/YTD TTM unless operating cash flow, capital
expenditure, stock-based compensation, and revenue are all present. It keeps
the current standalone-quarter strategy and returns whichever complete TTM is
newer. SEC-derived interest outside operating cash flow remains zero, so it is
not double-subtracted.

## Scope and safety

No route, schema, valuation model, DCF assumption, or yFinance fallback policy
changes. The owner-earnings quality gate remains strict; the change gives it
the complete current SEC data it already requires.

## Verification

- Regression-test comparative annual facts all labelled with a later filing's
  `fy`, asserting their fiscal-year identities remain historical.
- Regression-test an Apple-like, unframed fiscal Q2 annual/YTD sequence and
  assert its derived TTM values and owner-earnings eligibility.
- Preserve framed annual/YTD, standalone-quarter, and incomplete-window tests.
- Run the focused SEC and owner-earnings suites, the full suite, and the opt-in
  live AAPL valuation check.
