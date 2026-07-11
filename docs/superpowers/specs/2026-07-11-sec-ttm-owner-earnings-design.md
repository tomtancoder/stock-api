# SEC TTM owner-earnings design

## Goal

Create one conservative trailing-twelve-month (TTM) `FinancialPeriod` from SEC
Company Facts when a US issuer reports cash-flow facts cumulatively in 10-Q
filings. This allows the owner-earnings model to use an independent current
normalization component without relaxing its quality thresholds.

## Root cause

The existing SEC TTM builder only accepts four standalone quarterly duration
facts. Tesla's operating cash flow, capital expenditure, and stock-based
compensation are reported as year-to-date values in 10-Q filings: three months
in Q1, six months in Q2, and nine months in Q3. Its 10-K provides the annual
figure rather than a standalone Q4 fact. Consequently, the builder emits no
usable recent TTM cash-flow period.

## Design

For each additive field, construct TTM only when all three facts use the same
SEC concept and compatible currency:

```
TTM = prior fiscal-year annual - prior-year matching YTD + current-year matching YTD
```

The matching YTD period must end in the same fiscal quarter for consecutive
years. Q1, Q2, and Q3 are eligible. This produces a TTM ending on the current
YTD period end. It works with both quarterly and year-to-date SEC disclosures
and avoids mixing aliases or overlapping durations.

For balance-sheet fields, use the latest compatible instant fact at the TTM
end. For diluted shares, use the compatible duration fact ending at that date.
Interest paid remains zero only under the existing SEC accounting treatment.

The existing four-standalone-quarter route remains available as a fallback. No
TTM is emitted if a required owner-earnings cash-flow component cannot be
derived from one coherent annual/YTD relationship.

## Safety and testing

- Preserve SEC fact provenance for derived values, identifying the current
  filing as the primary source.
- Reject mixed concepts, nonmatching quarter ends, nonconsecutive fiscal years,
  incompatible currencies, and missing comparison periods.
- Add unit coverage for a Tesla-like annual plus Q1 year-to-date sequence and
  regression coverage proving owner-earnings valuation becomes eligible only
  with a complete derived TTM.
- Keep current tests for standalone quarterly TTM behavior green.

## Success criteria

With SEC facts for Tesla through 2026 Q1, the API creates a TTM period ending
2026-03-31, uses `sec_companyfacts` as the primary source, and returns an
owner-earnings DCF result rather than weakening the quality gate.
