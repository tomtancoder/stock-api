from __future__ import annotations

import math
import re
from collections.abc import Mapping
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.market_symbols import normalize_exchange, to_public_symbol
from app.services.valuation_types import (
    FactProvenance,
    FinancialPeriod,
    ValuationFundamentals,
)


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class SecCompanyFactsError(RuntimeError):
    """Raised when SEC company facts cannot be retrieved or normalized."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 502,
        retry_after_s: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.headers = (
            {"Retry-After": str(retry_after_s)}
            if retry_after_s is not None
            else None
        )


_TICKER_CACHE: tuple[float, dict[str, str]] | None = None
_CACHE_LOCK = Lock()

_ALLOWED_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})
_ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
_QUARTER_FRAME = re.compile(r"^CY(?P<year>\d{4})Q(?P<quarter>[1-4])(?:I)?$")

_CONCEPTS: dict[str, tuple[str, ...]] = {
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
    "stock_based_compensation": (
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ),
    "interest_paid_outside_operating": (
        "InterestPaidNet",
        "InterestPaid",
    ),
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income_common": (
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLoss",
    ),
    "common_equity": (
        "CommonStockholdersEquity",
        "StockholdersEquity",
        "PartnersCapital",
        "LimitedPartnersCapital",
        "MembersEquity",
    ),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "total_assets": ("Assets",),
    "total_debt": (
        "LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrentAndNoncurrent",
        "LongTermDebt",
    ),
    "diluted_shares": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        "WeightedAverageNumberOfUnitsOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ),
    "common_dividends": (
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDistributionsToCommonStockholders",
        "PaymentsOfDividends",
    ),
    "distribution_per_unit": (
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
        "DistributionsPerUnit",
    ),
    "nav_per_unit": (
        "NetAssetValuePerShare",
        "NetAssetValuePerUnit",
    ),
    "real_estate_depreciation": (
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "DepreciationDepletionAndAmortization",
    ),
    "gain_on_property_sales": (
        "GainLossOnSaleOfRealEstate",
        "GainLossOnSaleOfPropertyPlantEquipment",
    ),
}
_INSTANT_FIELDS = frozenset(
    {
        "common_equity",
        "cash_and_equivalents",
        "total_assets",
        "total_debt",
        "nav_per_unit",
    }
)
_ADDITIVE_FIELDS = frozenset(
    {
        "operating_cash_flow",
        "capital_expenditure",
        "stock_based_compensation",
        "revenue",
        "net_income_common",
        "common_dividends",
        "distribution_per_unit",
        "real_estate_depreciation",
        "gain_on_property_sales",
    }
)
_OWNER_EARNINGS_TTM_FIELDS = frozenset(
    {
        "operating_cash_flow",
        "capital_expenditure",
        "stock_based_compensation",
        "revenue",
    }
)
_FACT_FIELDS = tuple(_CONCEPTS)
_REIT_ONLY_FACT_FIELDS = frozenset({"distribution_per_unit", "nav_per_unit"})
_REIT_REQUIRED_FACT_FIELDS = (
    "distribution_per_unit",
    "diluted_shares",
    "nav_per_unit",
)
_OPTIONAL_FACT_FIELDS = frozenset(
    {"real_estate_depreciation", "gain_on_property_sales"}
)


@dataclass(frozen=True)
class _SecFact:
    concept: str
    unit: str
    value: float
    start: date | None
    end: date
    filed: date | None
    accession: str | None
    form: str
    fiscal_year: int | None
    fiscal_period: str | None
    frame: str | None


@dataclass(frozen=True)
class _TtmWindow:
    facts: tuple[_SecFact, _SecFact, _SecFact, _SecFact]
    final_frame_index: int
    field_priority: int
    concept_priority: int


@dataclass(frozen=True)
class _AnnualYtdTtmWindow:
    annual: _SecFact
    prior_ytd: _SecFact
    current_ytd: _SecFact
    field_priority: int
    concept_priority: int


def resolve_cik(symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    mapping = _ticker_mapping()
    cik = mapping.get(normalized_symbol)
    if cik is None:
        raise SecCompanyFactsError(
            f"SEC ticker mapping does not contain {normalized_symbol}",
            status_code=404,
        )
    return cik


def fetch_sec_fundamentals(
    exchange: str, symbol: str
) -> ValuationFundamentals:
    venue = normalize_exchange(exchange)
    cik = resolve_cik(symbol)
    facts_payload = _request_json(COMPANY_FACTS_URL.format(cik=cik))
    submissions_payload = _request_json(SUBMISSIONS_URL.format(cik=cik))
    concepts = _us_gaap_concepts(facts_payload)
    currency = _reporting_currency(concepts)
    annual_periods = _build_annual_periods(concepts, currency)
    ttm_period = _build_ttm_period(concepts, currency)
    periods = sorted(
        [*annual_periods, *([ttm_period] if ttm_period is not None else [])],
        key=lambda period: (period.period_end, period.is_ttm),
    )
    current_shares = _latest_diluted_shares(periods)
    metadata = _submission_metadata(submissions_payload)
    is_reit = _metadata_is_reit(metadata)
    required_fact_fields = (
        _REIT_REQUIRED_FACT_FIELDS
        if is_reit
        else tuple(
            field
            for field in _FACT_FIELDS
            if field not in _OPTIONAL_FACT_FIELDS
            and field not in _REIT_ONLY_FACT_FIELDS
        )
    )
    missing_fields = [
        field
        for field in required_fact_fields
        if not any(getattr(period, field) is not None for period in periods)
    ]
    if current_shares is None:
        missing_fields.append("current_diluted_shares")

    sources = {
        "financial_statements": "sec_companyfacts",
        "submissions_metadata": "sec_submissions",
    }
    if current_shares is not None:
        sources["current_diluted_shares"] = "sec_companyfacts"

    return ValuationFundamentals(
        symbol=to_public_symbol(venue, symbol),
        exchange=venue,
        currency=currency,
        primary_source="sec_companyfacts",
        provider_security_type=metadata["provider_security_type"],
        industry=metadata["industry"],
        issuer_classification=metadata["issuer_classification"],
        current_diluted_shares=current_shares,
        periods=periods,
        fetched_at=datetime.now(timezone.utc),
        sources=sources,
        missing_fields=missing_fields,
    )


def _us_gaap_concepts(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise SecCompanyFactsError("SEC Company Facts response is invalid")
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        raise SecCompanyFactsError("SEC Company Facts response has no facts map")
    concepts = facts.get("us-gaap")
    if not isinstance(concepts, Mapping):
        raise SecCompanyFactsError(
            "SEC Company Facts response has no us-gaap facts"
        )
    return concepts


def _reporting_currency(concepts: Mapping[str, Any]) -> str:
    counts: Counter[str] = Counter()
    monetary_concepts = {
        concept
        for field, names in _CONCEPTS.items()
        if field != "diluted_shares"
        for concept in names
    }
    for concept in monetary_concepts:
        units = _concept_units(concepts, concept)
        for raw_unit, raw_facts in units.items():
            unit = _text(raw_unit)
            if unit is None or re.fullmatch(r"[A-Z]{3}", unit) is None:
                continue
            if not isinstance(raw_facts, list):
                continue
            counts[unit] += sum(
                1
                for raw_fact in raw_facts
                if isinstance(raw_fact, Mapping)
                and raw_fact.get("form") in _ALLOWED_FORMS
                and _finite_float(raw_fact.get("val")) is not None
            )
    if not counts:
        return "USD"
    return max(
        counts,
        key=lambda unit: (counts[unit], unit == "USD", unit),
    )


def _build_annual_periods(
    concepts: Mapping[str, Any], currency: str
) -> list[FinancialPeriod]:
    candidates = {
        field: {
            concept: [
                fact
                for fact in _concept_facts(
                    concepts,
                    concept,
                    _units_for_field(field, currency),
                )
                if _is_annual_fact(fact, field)
            ]
            for concept in concept_names
        }
        for field, concept_names in _CONCEPTS.items()
    }
    period_ends = sorted(
        {
            fact.end
            for concepts_by_name in candidates.values()
            for facts in concepts_by_name.values()
            for fact in facts
        }
    )[-5:]
    periods: list[FinancialPeriod] = []
    for period_end in period_ends:
        values: dict[str, Any] = {
            "period_end": period_end,
            "fiscal_year": None,
            "is_ttm": False,
            "currency": currency,
        }
        sources: dict[str, FactProvenance] = {}
        fiscal_years: list[int] = []
        for field, concept_names in _CONCEPTS.items():
            selected = _select_for_period(
                candidates[field], concept_names, period_end, field
            )
            if selected is None:
                continue
            values[field] = (
                0.0
                if field == "interest_paid_outside_operating"
                else selected.value
            )
            sources[field] = _provenance(selected)
            if selected.fiscal_year is not None:
                fiscal_years.append(selected.fiscal_year)
        if not sources:
            continue
        values["fiscal_year"] = (
            Counter(fiscal_years).most_common(1)[0][0]
            if fiscal_years
            else period_end.year
        )
        values["sources"] = sources
        periods.append(FinancialPeriod(**values))
    return periods


def _build_ttm_period(
    concepts: Mapping[str, Any], currency: str
) -> FinancialPeriod | None:
    standalone = _build_standalone_quarter_ttm_period(concepts, currency)
    annual_ytd = _build_annual_ytd_ttm_period(concepts, currency)
    if standalone is None:
        return annual_ytd
    if annual_ytd is None:
        return standalone
    return standalone if standalone.period_end >= annual_ytd.period_end else annual_ytd


def _build_standalone_quarter_ttm_period(
    concepts: Mapping[str, Any], currency: str
) -> FinancialPeriod | None:
    all_quarters: dict[str, dict[str, list[_SecFact]]] = {
        field: {
            concept: [
                fact
                for fact in _concept_facts(
                    concepts,
                    concept,
                    _units_for_field(field, currency),
                )
                if _is_quarter_fact(fact, field)
            ]
            for concept in concept_names
        }
        for field, concept_names in _CONCEPTS.items()
    }
    window = _latest_ttm_window(all_quarters)
    if window is None:
        return None

    values: dict[str, Any] = {
        "fiscal_year": None,
        "is_ttm": True,
        "currency": currency,
    }
    sources: dict[str, FactProvenance] = {}
    for field, concept_names in _CONCEPTS.items():
        if field in _ADDITIVE_FIELDS or field == "interest_paid_outside_operating":
            facts = _select_same_concept_quarters(
                all_quarters[field], concept_names, window.facts
            )
            if facts is None:
                continue
            selected = facts[-1]
            values[field] = (
                0.0
                if field == "interest_paid_outside_operating"
                else sum(fact.value for fact in facts)
            )
        elif field == "diluted_shares":
            selected = _select_duration_at_window_end(
                all_quarters[field], concept_names, window.facts[-1]
            )
            if selected is None:
                continue
            values[field] = selected.value
        else:
            selected = _select_instant_at_window_end(
                all_quarters[field], concept_names, window.facts[-1].end
            )
            if selected is None:
                continue
            values[field] = selected.value
        sources[field] = _provenance(selected)

    if not sources:
        return None
    values["period_end"] = window.facts[-1].end
    values["sources"] = sources
    return FinancialPeriod(**values)


def _build_annual_ytd_ttm_period(
    concepts: Mapping[str, Any], currency: str
) -> FinancialPeriod | None:
    all_facts: dict[str, dict[str, list[_SecFact]]] = {
        field: {
            concept: _concept_facts(
                concepts,
                concept,
                _units_for_field(field, currency),
            )
            for concept in concept_names
        }
        for field, concept_names in _CONCEPTS.items()
    }
    window = _latest_annual_ytd_ttm_window(all_facts)
    if window is None:
        return None

    values: dict[str, Any] = {
        "fiscal_year": None,
        "is_ttm": True,
        "currency": currency,
        "period_end": window.current_ytd.end,
        "interest_paid_outside_operating": 0.0,
    }
    sources: dict[str, FactProvenance] = {}
    for field, concept_names in _CONCEPTS.items():
        if field in _ADDITIVE_FIELDS:
            facts = _select_annual_ytd_facts(
                all_facts[field], concept_names, field, window
            )
            if facts is None:
                continue
            annual, prior_ytd, current_ytd = facts
            values[field] = annual.value - prior_ytd.value + current_ytd.value
            sources[field] = _provenance(current_ytd)
        elif field == "interest_paid_outside_operating":
            continue
        elif field == "diluted_shares":
            selected = _select_duration_at_period_end(
                all_facts[field], concept_names, window.current_ytd.end
            )
            if selected is None:
                continue
            values[field] = selected.value
            sources[field] = _provenance(selected)
        else:
            selected = _select_instant_at_window_end(
                all_facts[field], concept_names, window.current_ytd.end
            )
            if selected is None:
                continue
            values[field] = selected.value
            sources[field] = _provenance(selected)

    if not _OWNER_EARNINGS_TTM_FIELDS.issubset(values):
        return None
    if not sources:
        return None
    values["sources"] = sources
    return FinancialPeriod(**values)


def _latest_annual_ytd_ttm_window(
    all_facts: Mapping[str, Mapping[str, list[_SecFact]]],
) -> _AnnualYtdTtmWindow | None:
    candidates: list[_AnnualYtdTtmWindow] = []
    for field_priority, (field, concept_names) in enumerate(_CONCEPTS.items()):
        if field in _INSTANT_FIELDS or field == "interest_paid_outside_operating":
            continue
        for concept_priority, concept in enumerate(concept_names):
            by_frame: dict[int, list[_SecFact]] = {}
            for fact in all_facts[field][concept]:
                frame_index = _frame_index(fact.frame)
                if frame_index is not None and _is_ytd_fact(fact):
                    by_frame.setdefault(frame_index, []).append(fact)
            observations = {
                frame_index: _latest_compatible_duration_fact(facts)
                for frame_index, facts in by_frame.items()
            }
            annual_facts = [
                fact
                for fact in all_facts[field][concept]
                if _is_annual_fact(fact, field)
            ]
            for current_frame_index, current_ytd in observations.items():
                prior_ytd = observations.get(current_frame_index - 4)
                if prior_ytd is None or current_ytd.start is None:
                    continue
                annual_candidates = [
                    fact
                    for fact in annual_facts
                    if prior_ytd.end < fact.end <= current_ytd.start
                ]
                if not annual_candidates:
                    continue
                candidates.append(
                    _AnnualYtdTtmWindow(
                        annual=max(
                            annual_candidates,
                            key=lambda fact: (fact.end, fact.filed or date.min),
                        ),
                        prior_ytd=prior_ytd,
                        current_ytd=current_ytd,
                        field_priority=field_priority,
                        concept_priority=concept_priority,
                    )
                )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda window: (
            window.current_ytd.end,
            _frame_index(window.current_ytd.frame) or -1,
            -window.field_priority,
            -window.concept_priority,
        ),
    )


def _select_annual_ytd_facts(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    field: str,
    window: _AnnualYtdTtmWindow,
) -> tuple[_SecFact, _SecFact, _SecFact] | None:
    for concept in concept_names:
        annual_candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.end == window.annual.end and _is_annual_fact(fact, field)
        ]
        prior_candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.frame == window.prior_ytd.frame
            and fact.start == window.prior_ytd.start
            and fact.end == window.prior_ytd.end
            and _is_ytd_fact(fact)
        ]
        current_candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.frame == window.current_ytd.frame
            and fact.start == window.current_ytd.start
            and fact.end == window.current_ytd.end
            and _is_ytd_fact(fact)
        ]
        if annual_candidates and prior_candidates and current_candidates:
            return (
                _latest_compatible_duration_fact(annual_candidates),
                _latest_compatible_duration_fact(prior_candidates),
                _latest_compatible_duration_fact(current_candidates),
            )
    return None


def _latest_ttm_window(
    all_quarters: Mapping[str, Mapping[str, list[_SecFact]]],
) -> _TtmWindow | None:
    candidates: list[_TtmWindow] = []
    for field_priority, (field, concept_names) in enumerate(_CONCEPTS.items()):
        if field in _INSTANT_FIELDS:
            continue
        for concept_priority, concept in enumerate(concept_names):
            by_frame: dict[int, list[_SecFact]] = {}
            for fact in all_quarters[field][concept]:
                frame_index = _frame_index(fact.frame)
                if frame_index is not None:
                    by_frame.setdefault(frame_index, []).append(fact)
            observations = {
                frame_index: _latest_compatible_duration_fact(facts)
                for frame_index, facts in by_frame.items()
            }
            frame_indices = sorted(observations)
            for position in range(len(frame_indices) - 3):
                run = frame_indices[position : position + 4]
                if any(right != left + 1 for left, right in zip(run, run[1:])):
                    continue
                facts = tuple(observations[index] for index in run)
                if not _coherent_quarter_window(facts):
                    continue
                candidates.append(
                    _TtmWindow(
                        facts=facts,
                        final_frame_index=run[-1],
                        field_priority=field_priority,
                        concept_priority=concept_priority,
                    )
                )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda window: (
            window.facts[-1].end,
            window.final_frame_index,
            -window.field_priority,
            -window.concept_priority,
        ),
    )


def _coherent_quarter_window(
    facts: tuple[_SecFact, _SecFact, _SecFact, _SecFact],
) -> bool:
    if any(fact.start is None for fact in facts):
        return False
    for previous, current in zip(facts, facts[1:]):
        assert previous.start is not None
        assert current.start is not None
        gap_days = (current.start - previous.end).days
        if gap_days != 1 or current.end <= previous.end:
            return False
    assert facts[0].start is not None
    return 330 <= (facts[-1].end - facts[0].start).days <= 400


def _select_same_concept_quarters(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    window_facts: tuple[_SecFact, _SecFact, _SecFact, _SecFact],
) -> list[_SecFact] | None:
    for concept in concept_names:
        selected: list[_SecFact] = []
        for window_fact in window_facts:
            candidates = [
                fact
                for fact in concepts_by_name[concept]
                if fact.start == window_fact.start and fact.end == window_fact.end
            ]
            if not candidates:
                break
            selected.append(_latest_compatible_duration_fact(candidates))
        if len(selected) != 4:
            continue
        return selected
    return None


def _select_duration_at_window_end(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    window_fact: _SecFact,
) -> _SecFact | None:
    for concept in concept_names:
        candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.start == window_fact.start and fact.end == window_fact.end
        ]
        if candidates:
            return _latest_compatible_duration_fact(candidates)
    return None


def _select_duration_at_period_end(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    period_end: date,
) -> _SecFact | None:
    for concept in concept_names:
        candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.end == period_end and fact.start is not None
        ]
        if candidates:
            return _latest_compatible_duration_fact(candidates)
    return None


def _select_instant_at_window_end(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    period_end: date,
) -> _SecFact | None:
    for concept in concept_names:
        candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.end == period_end
        ]
        if candidates:
            return _latest_fact(candidates)
    return None


def _select_for_period(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    period_end: date,
    field: str,
) -> _SecFact | None:
    for concept in concept_names:
        candidates = [
            fact
            for fact in concepts_by_name[concept]
            if fact.end == period_end
        ]
        if candidates:
            return _latest_compatible_annual_fact(candidates, field)
    return None


def _latest_compatible_annual_fact(
    candidates: list[_SecFact], field: str
) -> _SecFact:
    if field in _INSTANT_FIELDS:
        return _latest_fact(candidates)
    return _latest_compatible_duration_fact(candidates)


def _latest_compatible_duration_fact(
    candidates: list[_SecFact],
) -> _SecFact:
    originals = [fact for fact in candidates if not fact.form.endswith("/A")]
    if not originals:
        return _latest_fact(candidates)
    original = _latest_fact(originals)
    compatible = [
        fact
        for fact in candidates
        if fact.start == original.start and fact.end == original.end
    ]
    return _latest_fact(compatible)


def _latest_fact(candidates: list[_SecFact]) -> _SecFact:
    return max(
        candidates,
        key=lambda fact: (
            fact.filed or date.min,
            fact.form.endswith("/A"),
            fact.accession or "",
            fact.start or date.min,
            fact.value,
        ),
    )


def _concept_facts(
    concepts: Mapping[str, Any], concept: str, accepted_units: tuple[str, ...]
) -> list[_SecFact]:
    units = _concept_units(concepts, concept)
    parsed: list[_SecFact] = []
    for unit in accepted_units:
        raw_facts = units.get(unit)
        if not isinstance(raw_facts, list):
            continue
        for raw_fact in raw_facts:
            if not isinstance(raw_fact, Mapping):
                continue
            form = _text(raw_fact.get("form"))
            end = _date(raw_fact.get("end"))
            value = _finite_float(raw_fact.get("val"))
            if form not in _ALLOWED_FORMS or end is None or value is None:
                continue
            parsed.append(
                _SecFact(
                    concept=concept,
                    unit=unit,
                    value=value,
                    start=_date(raw_fact.get("start")),
                    end=end,
                    filed=_date(raw_fact.get("filed")),
                    accession=_text(raw_fact.get("accn")),
                    form=form,
                    fiscal_year=_integer(raw_fact.get("fy")),
                    fiscal_period=_text(raw_fact.get("fp")),
                    frame=_text(raw_fact.get("frame")),
                )
            )
    return parsed


def _concept_units(
    concepts: Mapping[str, Any], concept: str
) -> Mapping[str, Any]:
    details = concepts.get(concept)
    if not isinstance(details, Mapping):
        return {}
    units = details.get("units")
    return units if isinstance(units, Mapping) else {}


def _is_annual_fact(fact: _SecFact, field: str) -> bool:
    if fact.form not in _ANNUAL_FORMS:
        return False
    if fact.fiscal_period is not None and fact.fiscal_period != "FY":
        return False
    if field in _INSTANT_FIELDS:
        return True
    if fact.start is None:
        return False
    return 250 <= (fact.end - fact.start).days <= 450


def _is_quarter_fact(fact: _SecFact, field: str) -> bool:
    if _frame_index(fact.frame) is None:
        return False
    if field in _INSTANT_FIELDS:
        return True
    if fact.start is None:
        return False
    return 75 <= (fact.end - fact.start).days <= 105


def _is_ytd_fact(fact: _SecFact) -> bool:
    frame_index = _frame_index(fact.frame)
    if frame_index is None or fact.start is None:
        return False
    quarter = (frame_index % 4) + 1
    if quarter not in {1, 2, 3}:
        return False
    return 75 <= (fact.end - fact.start).days <= 300


def _frame_index(frame: str | None) -> int | None:
    if frame is None:
        return None
    match = _QUARTER_FRAME.fullmatch(frame)
    if match is None:
        return None
    return int(match.group("year")) * 4 + int(match.group("quarter")) - 1


def _units_for_field(field: str, currency: str) -> tuple[str, ...]:
    if field == "diluted_shares":
        return ("shares", "units")
    if field in {"distribution_per_unit", "nav_per_unit"}:
        return (
            f"{currency}/shares",
            f"{currency}/unit",
            f"{currency}/units",
        )
    return (currency,)


def _provenance(fact: _SecFact) -> FactProvenance:
    return FactProvenance(
        provider="sec_companyfacts",
        concept=fact.concept,
        form=fact.form,
        accession=fact.accession,
        period_end=fact.end,
        filed_at=fact.filed,
        unit=fact.unit,
    )


def _latest_diluted_shares(periods: list[FinancialPeriod]) -> float | None:
    candidates = [
        period
        for period in periods
        if period.diluted_shares is not None and period.diluted_shares > 0
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda period: (period.period_end, period.is_ttm))
    return latest.diluted_shares


def _submission_metadata(payload: Any) -> dict[str, str | None]:
    if not isinstance(payload, Mapping):
        raise SecCompanyFactsError("SEC submissions response is invalid")
    entity_type = _text(payload.get("entityType"))
    sic = _text(payload.get("sic"))
    description = _text(payload.get("sicDescription"))
    if description and sic:
        classification = f"{description} (SIC {sic})"
    else:
        classification = description or (f"SIC {sic}" if sic else entity_type)
    return {
        "provider_security_type": entity_type,
        "industry": description,
        "issuer_classification": classification,
    }


def _metadata_is_reit(metadata: Mapping[str, str | None]) -> bool:
    evidence = " ".join(
        value.casefold() for value in metadata.values() if value is not None
    )
    compact = evidence.replace("_", "").replace("-", "").replace(" ", "")
    return any(
        term in evidence or term.replace(" ", "") in compact
        for term in (
            "reit",
            "real estate investment trust",
            "property trust",
        )
    )


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _ticker_mapping() -> dict[str, str]:
    global _TICKER_CACHE

    now = monotonic()
    with _CACHE_LOCK:
        cached = _TICKER_CACHE
        if cached is not None and cached[0] > now:
            return dict(cached[1])

    payload = _request_json(TICKERS_URL)
    mapping = _normalize_ticker_mapping(payload)
    expires_at = monotonic() + get_settings().valuation_cache_ttl_seconds
    with _CACHE_LOCK:
        _TICKER_CACHE = (expires_at, mapping)
    return dict(mapping)


def _request_json(url: str) -> Any:
    user_agent = get_settings().sec_user_agent
    if user_agent is None or not user_agent.strip():
        raise SecCompanyFactsError(
            "SEC requests require STOCK_API_SEC_USER_AGENT"
        )
    try:
        with httpx.Client(
            timeout=20.0,
            headers={"User-Agent": user_agent},
        ) as client:
            response = client.get(url)
            if response.status_code == 429:
                headers = getattr(response, "headers", None)
                retry_after = (
                    headers.get("Retry-After")
                    if isinstance(headers, Mapping)
                    else None
                )
                raise SecCompanyFactsError(
                    f"SEC request rate limited for {url}",
                    retry_after_s=_retry_after_s(retry_after),
                )
            response.raise_for_status()
            return response.json()
    except SecCompanyFactsError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize provider failures.
        raise SecCompanyFactsError(f"SEC request failed for {url}: {exc}") from exc


def _retry_after_s(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if re.fullmatch(r"\d+", candidate):
        return int(candidate)
    try:
        retry_at = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        return None
    delay = (retry_at.astimezone(timezone.utc) - _utc_now()).total_seconds()
    return max(0, math.ceil(delay))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_ticker_mapping(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise SecCompanyFactsError("SEC ticker mapping response is invalid")

    mapping: dict[str, str] = {}
    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if not isinstance(ticker, str):
            continue
        try:
            normalized_cik = f"{int(cik):010d}"
        except (TypeError, ValueError):
            continue
        mapping[ticker.strip().upper()] = normalized_cik
    return mapping


def _clear_cache() -> None:
    global _TICKER_CACHE

    with _CACHE_LOCK:
        _TICKER_CACHE = None
