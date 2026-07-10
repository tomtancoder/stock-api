from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from numbers import Real
from threading import Event, Lock
from time import monotonic

from app.core.config import get_settings
from app.services.market_symbols import normalize_exchange, to_public_symbol
from app.services.sec_companyfacts import (
    SecCompanyFactsError,
    fetch_sec_fundamentals,
)
from app.services.valuation_types import (
    APPROVED_BANK_METRIC_KEYS,
    APPROVED_REIT_METRIC_KEYS,
    FactProvenance,
    FinancialPeriod,
    ValuationFundamentals,
)
from app.services.yfinance_statements import (
    YFinanceStatementsError,
    fetch_yfinance_fundamentals,
)


@dataclass(frozen=True, slots=True)
class FundamentalsEnvelope:
    fundamentals: ValuationFundamentals
    fresh_until: datetime
    stale_until: datetime
    stale: bool
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _RefreshFlight:
    event: Event
    generation: int
    result: FundamentalsEnvelope | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    envelope: FundamentalsEnvelope
    fresh_deadline: float
    stale_deadline: float


_SEC_EXCHANGES = frozenset({"AMEX", "NASDAQ", "NYSE", "NYSEAMERICAN"})
_PERIOD_FACT_FIELDS = (
    "operating_cash_flow",
    "capital_expenditure",
    "stock_based_compensation",
    "interest_paid_outside_operating",
    "revenue",
    "net_income_common",
    "common_equity",
    "cash_and_equivalents",
    "total_assets",
    "total_debt",
    "diluted_shares",
    "common_dividends",
    "distribution_per_unit",
    "nav_per_unit",
    "real_estate_depreciation",
    "gain_on_property_sales",
)
_METADATA_FIELDS = (
    "provider_security_type",
    "sector",
    "industry",
    "issuer_classification",
)
_PROVIDER_ERRORS = (SecCompanyFactsError, YFinanceStatementsError)

_CACHE: dict[tuple[str, str], _CacheEntry] = {}
_IN_FLIGHT: dict[tuple[str, str], _RefreshFlight] = {}
_CACHE_GENERATION = 0
_CACHE_LOCK = Lock()


def get_fundamentals(exchange: str, symbol: str) -> FundamentalsEnvelope:
    venue = normalize_exchange(exchange)
    public_symbol = to_public_symbol(venue, symbol)
    key = (venue, public_symbol)
    flight: _RefreshFlight
    while True:
        now = monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and now < cached.fresh_deadline:
                return _clone_envelope(cached.envelope)
            flight = _IN_FLIGHT.get(key)
            if flight is None:
                flight = _RefreshFlight(
                    event=Event(), generation=_CACHE_GENERATION
                )
                _IN_FLIGHT[key] = flight
                break
        flight.event.wait()
        if flight.result is not None:
            return _clone_envelope(flight.result)
        if flight.error is not None:
            raise flight.error

    try:
        fundamentals, source_warnings = _fetch_uncached(venue, symbol)
        settings = get_settings()
        fresh_ttl = int(settings.valuation_cache_ttl_seconds)
        stale_ttl = max(
            fresh_ttl, int(settings.valuation_stale_ttl_seconds)
        )
        stored_at = monotonic()
        wall_time = datetime.now(timezone.utc)
        warnings = _unique((*fundamentals.warnings, *source_warnings))
        normalized = fundamentals.model_copy(
            deep=True, update={"warnings": list(warnings)}
        )
        envelope = FundamentalsEnvelope(
            fundamentals=normalized,
            fresh_until=wall_time + timedelta(seconds=fresh_ttl),
            stale_until=wall_time + timedelta(seconds=stale_ttl),
            stale=False,
            warnings=warnings,
        )
        entry = _CacheEntry(
            envelope=envelope,
            fresh_deadline=stored_at + fresh_ttl,
            stale_deadline=stored_at + stale_ttl,
        )
    except BaseException as exc:  # noqa: BLE001 - release all waiting callers.
        return _complete_failed_refresh(key, flight, exc)
    return _complete_successful_refresh(key, flight, entry)


def _complete_successful_refresh(
    key: tuple[str, str],
    flight: _RefreshFlight,
    entry: _CacheEntry,
) -> FundamentalsEnvelope:
    with _CACHE_LOCK:
        owns_generation = (
            _IN_FLIGHT.get(key) is flight
            and flight.generation == _CACHE_GENERATION
        )
        current = _CACHE.get(key)
        if owns_generation:
            _CACHE[key] = entry
            result = entry.envelope
            del _IN_FLIGHT[key]
        elif current is not None and monotonic() < current.fresh_deadline:
            result = current.envelope
        else:
            result = entry.envelope
        flight.result = result
        flight.event.set()
    return _clone_envelope(result)


def _complete_failed_refresh(
    key: tuple[str, str],
    flight: _RefreshFlight,
    exc: BaseException,
) -> FundamentalsEnvelope:
    with _CACHE_LOCK:
        current = _CACHE.get(key)
        now = monotonic()
        result: FundamentalsEnvelope | None = None
        error: BaseException | None = exc
        if current is not None and now < current.fresh_deadline:
            result = current.envelope
            error = None
        elif (
            isinstance(exc, _PROVIDER_ERRORS)
            and current is not None
            and now < current.stale_deadline
        ):
            warning = (
                "Fundamentals refresh failed; serving stale cached data: "
                f"{exc}"
            )
            result = FundamentalsEnvelope(
                fundamentals=current.envelope.fundamentals,
                fresh_until=current.envelope.fresh_until,
                stale_until=current.envelope.stale_until,
                stale=True,
                warnings=_unique((*current.envelope.warnings, warning)),
            )
            error = None
        if _IN_FLIGHT.get(key) is flight:
            del _IN_FLIGHT[key]
        flight.result = result
        flight.error = error
        flight.event.set()
    if result is not None:
        return _clone_envelope(result)
    raise exc


def _clone_envelope(envelope: FundamentalsEnvelope) -> FundamentalsEnvelope:
    return FundamentalsEnvelope(
        fundamentals=envelope.fundamentals.model_copy(deep=True),
        fresh_until=envelope.fresh_until,
        stale_until=envelope.stale_until,
        stale=envelope.stale,
        warnings=tuple(envelope.warnings),
    )


def _fetch_uncached(
    exchange: str, symbol: str
) -> tuple[ValuationFundamentals, tuple[str, ...]]:
    settings = get_settings()
    if exchange in _SEC_EXCHANGES:
        if not settings.sec_user_agent or not settings.sec_user_agent.strip():
            warning = (
                "STOCK_API_SEC_USER_AGENT is not configured; using yFinance "
                "fallback fundamentals and capping confidence below high."
            )
            fallback = fetch_yfinance_fundamentals(exchange, symbol)
            return _finalize_fundamentals(fallback), (warning,)

        primary = _normalize_provider_metrics(
            fetch_sec_fundamentals(exchange, symbol)
        )
        if not _needs_yfinance_fallback(primary):
            return _derive_reit_period_values(primary), ()
        try:
            fallback = _normalize_provider_metrics(
                fetch_yfinance_fundamentals(exchange, symbol)
            )
        except YFinanceStatementsError as exc:
            warning = f"Optional yFinance fundamentals fallback failed: {exc}"
            return _derive_reit_period_values(primary), (warning,)
        return _merge_sec_with_yfinance(primary, fallback)

    fundamentals = _finalize_fundamentals(
        fetch_yfinance_fundamentals(exchange, symbol)
    )
    if exchange == "SGX":
        return fundamentals, (
            "SGX yFinance fundamentals cap valuation confidence at medium.",
        )
    return fundamentals, ()


def _needs_yfinance_fallback(fundamentals: ValuationFundamentals) -> bool:
    return bool(
        fundamentals.missing_fields
        or fundamentals.current_diluted_shares is None
        or any(getattr(fundamentals, field) is None for field in _METADATA_FIELDS)
    )


def _merge_sec_with_yfinance(
    primary: ValuationFundamentals,
    fallback: ValuationFundamentals,
) -> tuple[ValuationFundamentals, tuple[str, ...]]:
    primary = _normalize_provider_metrics(primary)
    fallback = _normalize_provider_metrics(fallback)
    warnings = [*primary.warnings, *fallback.warnings]
    if (
        primary.symbol != fallback.symbol
        or primary.exchange != fallback.exchange
    ):
        warnings.append(
            "Ignored yFinance fundamentals fallback because its symbol or "
            "exchange did not match the SEC result."
        )
        return primary, _unique(warnings)

    currency = primary.currency.strip().upper()
    if fallback.currency.strip().upper() != currency:
        warnings.append(
            "Ignored yFinance fundamentals fallback currency "
            f"{fallback.currency}; expected {primary.currency}."
        )
        return primary, _unique(warnings)

    fallback_periods = {
        (period.period_end, period.is_ttm): period
        for period in fallback.periods
        if period.currency.strip().upper() == currency
    }
    merged_periods: list[FinancialPeriod] = []
    filled_fields: set[str] = set()
    for primary_period in primary.periods:
        fallback_period = fallback_periods.get(
            (primary_period.period_end, primary_period.is_ttm)
        )
        if (
            fallback_period is None
            or primary_period.currency.strip().upper() != currency
        ):
            merged_periods.append(primary_period)
            continue

        updates: dict[str, object] = {}
        sources = dict(primary_period.sources)
        for field in _PERIOD_FACT_FIELDS:
            if getattr(primary_period, field) is not None:
                continue
            fallback_value = getattr(fallback_period, field)
            if fallback_value is None:
                continue
            provenance = fallback_period.sources.get(field)
            if not _compatible_provenance(
                field,
                provenance_unit=provenance.unit if provenance else None,
                provenance_period_end=(
                    provenance.period_end if provenance else None
                ),
                expected_currency=currency,
                expected_period_end=primary_period.period_end,
            ):
                warnings.append(
                    "Ignored yFinance fallback fact "
                    f"{field} for {primary_period.period_end} because its "
                    "unit or period provenance was incompatible."
                )
                continue
            updates[field] = fallback_value
            sources[field] = provenance
            filled_fields.add(field)
        if updates:
            updates["sources"] = sources
            merged_periods.append(primary_period.model_copy(update=updates))
        else:
            merged_periods.append(primary_period)

    top_level_updates: dict[str, object] = {"periods": merged_periods}
    sources = dict(primary.sources)
    for field in _METADATA_FIELDS:
        if (
            getattr(primary, field) is None
            and getattr(fallback, field) is not None
        ):
            top_level_updates[field] = getattr(fallback, field)
            sources[field] = fallback.primary_source
            filled_fields.add(field)
    if (
        primary.current_diluted_shares is None
        and fallback.current_diluted_shares is not None
    ):
        top_level_updates["current_diluted_shares"] = (
            fallback.current_diluted_shares
        )
        sources["current_diluted_shares"] = fallback.sources.get(
            "current_diluted_shares", fallback.primary_source
        )
        filled_fields.add("current_diluted_shares")

    bank_metrics = dict(primary.bank_metrics)
    for metric, value in fallback.bank_metrics.items():
        if metric in bank_metrics:
            continue
        bank_metrics[metric] = value
        sources[metric] = fallback.sources.get(
            metric, fallback.primary_source
        )
        filled_fields.add(metric)
    top_level_updates["bank_metrics"] = bank_metrics

    reit_metrics = dict(primary.reit_metrics)
    for metric, value in fallback.reit_metrics.items():
        if metric in reit_metrics:
            continue
        reit_metrics[metric] = value
        sources[metric] = fallback.sources.get(
            metric, fallback.primary_source
        )
        filled_fields.add(metric)
    top_level_updates["reit_metrics"] = reit_metrics

    for period in merged_periods:
        for field, provenance in period.sources.items():
            sources[field] = provenance.provider
    if filled_fields:
        sources["fallback_financial_statements"] = fallback.sources.get(
            "financial_statements", fallback.primary_source
        )
        warnings.append(
            "Filled missing SEC facts or metadata from a same-currency "
            "yFinance fallback."
        )
    top_level_updates["sources"] = sources

    candidate = primary.model_copy(update=top_level_updates)
    candidate = _derive_reit_period_values(candidate)
    candidate = candidate.model_copy(
        update={
            "missing_fields": _remaining_missing_fields(candidate),
            "warnings": list(_unique(warnings)),
        }
    )
    return candidate, _unique(warnings)


def _normalize_bank_metrics(
    fundamentals: ValuationFundamentals,
) -> ValuationFundamentals:
    bank_metrics = {
        key: float(value)
        for key, value in fundamentals.bank_metrics.items()
        if key in APPROVED_BANK_METRIC_KEYS
        and isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    sources = dict(fundamentals.sources)
    for metric in APPROVED_BANK_METRIC_KEYS:
        if metric not in bank_metrics:
            sources.pop(metric, None)
            continue
        source = sources.get(metric)
        if not isinstance(source, str) or not source.strip():
            sources[metric] = fundamentals.primary_source
    return fundamentals.model_copy(
        deep=True,
        update={"bank_metrics": bank_metrics, "sources": sources},
    )


def _normalize_reit_metrics(
    fundamentals: ValuationFundamentals,
) -> ValuationFundamentals:
    reit_metrics = {
        key: float(value)
        for key, value in fundamentals.reit_metrics.items()
        if key in APPROVED_REIT_METRIC_KEYS
        and isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    sources = dict(fundamentals.sources)
    metric_source_keys = (
        set(APPROVED_REIT_METRIC_KEYS) | set(fundamentals.reit_metrics)
    )
    for metric in metric_source_keys:
        if metric not in reit_metrics:
            sources.pop(metric, None)
            continue
        source = sources.get(metric)
        if not isinstance(source, str) or not source.strip():
            sources[metric] = fundamentals.primary_source
    return fundamentals.model_copy(
        deep=True,
        update={"reit_metrics": reit_metrics, "sources": sources},
    )


def _normalize_provider_metrics(
    fundamentals: ValuationFundamentals,
) -> ValuationFundamentals:
    return _normalize_reit_metrics(_normalize_bank_metrics(fundamentals))


def _finalize_fundamentals(
    fundamentals: ValuationFundamentals,
) -> ValuationFundamentals:
    normalized = _normalize_provider_metrics(fundamentals)
    derived = _derive_reit_period_values(normalized)
    return derived.model_copy(
        update={"missing_fields": _remaining_missing_fields(derived)}
    )


def _derive_reit_period_values(
    fundamentals: ValuationFundamentals,
) -> ValuationFundamentals:
    if not _is_reit(fundamentals):
        return fundamentals
    currency = fundamentals.currency.strip().upper()
    periods: list[FinancialPeriod] = []
    top_sources = dict(fundamentals.sources)
    for period in fundamentals.periods:
        if period.currency.strip().upper() != currency:
            periods.append(period)
            continue
        updates: dict[str, object] = {}
        period_sources = dict(period.sources)
        units = _positive_real(period.diluted_shares)
        units_compatible = _period_fact_is_compatible(
            period, "diluted_shares", currency
        )
        if (
            period.distribution_per_unit is None
            and units is not None
            and units_compatible
        ):
            distributions = _positive_real(period.common_dividends)
            if distributions is not None and _period_fact_is_compatible(
                period, "common_dividends", currency
            ):
                updates["distribution_per_unit"] = distributions / units
                period_sources["distribution_per_unit"] = FactProvenance(
                    provider="valuation_fundamentals",
                    concept="derived_distribution_per_unit",
                    form="derived",
                    period_end=period.period_end,
                    unit=f"{period.currency}/unit",
                )
                top_sources["distribution_per_unit"] = (
                    "derived_distribution_per_unit"
                )
        if (
            period.nav_per_unit is None
            and units is not None
            and units_compatible
        ):
            equity = _positive_real(period.common_equity)
            if equity is not None and _period_fact_is_compatible(
                period, "common_equity", currency
            ):
                updates["nav_per_unit"] = equity / units
                period_sources["nav_per_unit"] = FactProvenance(
                    provider="valuation_fundamentals",
                    concept="derived_nav_per_unit",
                    form="derived",
                    period_end=period.period_end,
                    unit=f"{period.currency}/unit",
                )
                top_sources["nav_per_unit"] = "derived_nav_per_unit"
        if updates:
            updates["sources"] = period_sources
            periods.append(period.model_copy(update=updates))
        else:
            periods.append(period)
    return fundamentals.model_copy(
        update={"periods": periods, "sources": top_sources}
    )


def _is_reit(fundamentals: ValuationFundamentals) -> bool:
    evidence = " ".join(
        value.casefold()
        for value in (
            fundamentals.provider_security_type,
            fundamentals.industry,
            fundamentals.issuer_classification,
        )
        if value is not None
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


def _positive_real(value: object) -> float | None:
    if not isinstance(value, Real) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _period_fact_is_compatible(
    period: FinancialPeriod,
    field: str,
    currency: str,
) -> bool:
    provenance = period.sources.get(field)
    return _compatible_provenance(
        field,
        provenance_unit=provenance.unit if provenance else None,
        provenance_period_end=provenance.period_end if provenance else None,
        expected_currency=currency,
        expected_period_end=period.period_end,
    )


def _compatible_provenance(
    field: str,
    *,
    provenance_unit: str | None,
    provenance_period_end: date | None,
    expected_currency: str,
    expected_period_end: date,
) -> bool:
    if provenance_unit is None or provenance_period_end != expected_period_end:
        return False
    normalized_unit = provenance_unit.strip().upper()
    if field == "diluted_shares":
        return normalized_unit in {"SHARES", "UNITS"}
    if field in {"distribution_per_unit", "nav_per_unit"}:
        return normalized_unit in {
            f"{expected_currency}/SHARE",
            f"{expected_currency}/SHARES",
            f"{expected_currency}/UNIT",
            f"{expected_currency}/UNITS",
        }
    return normalized_unit == expected_currency


def _remaining_missing_fields(
    fundamentals: ValuationFundamentals,
) -> list[str]:
    remaining: list[str] = []
    reit_missing_fields = {
        "distribution_per_unit",
        "diluted_shares",
        "nav_per_unit",
        "current_diluted_shares",
    }
    is_reit = _is_reit(fundamentals)
    for field in fundamentals.missing_fields:
        if is_reit and field not in reit_missing_fields:
            continue
        if field == "current_diluted_shares":
            if fundamentals.current_diluted_shares is None:
                remaining.append(field)
            continue
        if field not in _PERIOD_FACT_FIELDS:
            remaining.append(field)
            continue
        if not any(
            getattr(period, field) is not None
            for period in fundamentals.periods
        ):
            remaining.append(field)
    return remaining


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _clear_cache() -> None:
    global _CACHE_GENERATION

    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_GENERATION += 1
        flights = tuple(_IN_FLIGHT.values())
        _IN_FLIGHT.clear()
        for flight in flights:
            flight.event.set()
