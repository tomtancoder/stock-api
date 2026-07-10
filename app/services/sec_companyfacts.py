from __future__ import annotations

import math
import re
from collections.abc import Mapping
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
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

    def __init__(self, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


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
        "StockholdersEquity",
        "CommonStockholdersEquity",
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
    ),
    "common_dividends": (
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ),
}
_INSTANT_FIELDS = frozenset(
    {
        "common_equity",
        "cash_and_equivalents",
        "total_assets",
        "total_debt",
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
    }
)
_FACT_FIELDS = tuple(_CONCEPTS)


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
    missing_fields = [
        field
        for field in _FACT_FIELDS
        if not any(getattr(period, field) is not None for period in periods)
    ]
    if current_shares is None:
        missing_fields.append("current_diluted_shares")

    metadata = _submission_metadata(submissions_payload)
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
                    _unit_for_field(field, currency),
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
                candidates[field], concept_names, period_end
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
    all_quarters: dict[str, dict[str, list[_SecFact]]] = {
        field: {
            concept: [
                fact
                for fact in _concept_facts(
                    concepts,
                    concept,
                    _unit_for_field(field, currency),
                )
                if _is_quarter_fact(fact, field)
            ]
            for concept in concept_names
        }
        for field, concept_names in _CONCEPTS.items()
    }
    frame_run = _latest_four_quarter_run(all_quarters)
    if frame_run is None:
        return None

    values: dict[str, Any] = {
        "fiscal_year": None,
        "is_ttm": True,
        "currency": currency,
    }
    sources: dict[str, FactProvenance] = {}
    selected_facts: list[_SecFact] = []
    for field, concept_names in _CONCEPTS.items():
        if field in _ADDITIVE_FIELDS or field == "interest_paid_outside_operating":
            facts = _select_same_concept_quarters(
                all_quarters[field], concept_names, frame_run
            )
            if facts is None:
                continue
            selected = facts[-1]
            values[field] = (
                0.0
                if field == "interest_paid_outside_operating"
                else sum(fact.value for fact in facts)
            )
        else:
            selected = _select_latest_quarter(
                all_quarters[field], concept_names, frame_run[-1]
            )
            if selected is None:
                continue
            values[field] = selected.value
        selected_facts.append(selected)
        sources[field] = _provenance(selected)

    if not sources:
        return None
    values["period_end"] = max(fact.end for fact in selected_facts)
    values["sources"] = sources
    return FinancialPeriod(**values)


def _latest_four_quarter_run(
    all_quarters: Mapping[str, Mapping[str, list[_SecFact]]],
) -> tuple[int, int, int, int] | None:
    indices = sorted(
        {
            index
            for concepts_by_name in all_quarters.values()
            for facts in concepts_by_name.values()
            for fact in facts
            if (index := _frame_index(fact.frame)) is not None
        }
    )
    run: list[int] = []
    latest: tuple[int, int, int, int] | None = None
    for index in indices:
        if run and index != run[-1] + 1:
            run = []
        run.append(index)
        if len(run) >= 4:
            latest = tuple(run[-4:])  # type: ignore[assignment]
    return latest


def _select_same_concept_quarters(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    frame_run: tuple[int, int, int, int],
) -> list[_SecFact] | None:
    for concept in concept_names:
        by_frame: dict[int, list[_SecFact]] = {}
        for fact in concepts_by_name[concept]:
            index = _frame_index(fact.frame)
            if index is not None:
                by_frame.setdefault(index, []).append(fact)
        if not all(index in by_frame for index in frame_run):
            continue
        return [_latest_fact(by_frame[index]) for index in frame_run]
    return None


def _select_latest_quarter(
    concepts_by_name: Mapping[str, list[_SecFact]],
    concept_names: tuple[str, ...],
    frame_index: int,
) -> _SecFact | None:
    for concept in concept_names:
        candidates = [
            fact
            for fact in concepts_by_name[concept]
            if _frame_index(fact.frame) == frame_index
        ]
        if candidates:
            return _latest_fact(candidates)
    return None


def _select_for_period(
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
    concepts: Mapping[str, Any], concept: str, unit: str
) -> list[_SecFact]:
    units = _concept_units(concepts, concept)
    raw_facts = units.get(unit)
    if not isinstance(raw_facts, list):
        return []
    parsed: list[_SecFact] = []
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
    return 60 <= (fact.end - fact.start).days <= 120


def _frame_index(frame: str | None) -> int | None:
    if frame is None:
        return None
    match = _QUARTER_FRAME.fullmatch(frame)
    if match is None:
        return None
    return int(match.group("year")) * 4 + int(match.group("quarter")) - 1


def _unit_for_field(field: str, currency: str) -> str:
    return "shares" if field == "diluted_shares" else currency


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
            response.raise_for_status()
            return response.json()
    except SecCompanyFactsError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize provider failures.
        raise SecCompanyFactsError(f"SEC request failed for {url}: {exc}") from exc


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
