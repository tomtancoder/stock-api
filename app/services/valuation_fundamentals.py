from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from app.core.config import get_settings
from app.services.market_symbols import normalize_exchange, to_public_symbol
from app.services.sec_companyfacts import (
    SecCompanyFactsError,
    fetch_sec_fundamentals,
)
from app.services.valuation_types import FinancialPeriod, ValuationFundamentals
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
)
_METADATA_FIELDS = (
    "provider_security_type",
    "sector",
    "industry",
    "issuer_classification",
)
_PROVIDER_ERRORS = (SecCompanyFactsError, YFinanceStatementsError)

_CACHE: dict[tuple[str, str], _CacheEntry] = {}
_CACHE_LOCK = Lock()


def get_fundamentals(exchange: str, symbol: str) -> FundamentalsEnvelope:
    venue = normalize_exchange(exchange)
    public_symbol = to_public_symbol(venue, symbol)
    key = (venue, public_symbol)
    now = monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now < cached.fresh_deadline:
            return cached.envelope

    try:
        fundamentals, source_warnings = _fetch_uncached(venue, symbol)
    except _PROVIDER_ERRORS as exc:
        if cached is not None and monotonic() < cached.stale_deadline:
            warning = (
                "Fundamentals refresh failed; serving stale cached data: "
                f"{exc}"
            )
            return FundamentalsEnvelope(
                fundamentals=cached.envelope.fundamentals,
                fresh_until=cached.envelope.fresh_until,
                stale_until=cached.envelope.stale_until,
                stale=True,
                warnings=_unique((*cached.envelope.warnings, warning)),
            )
        raise

    settings = get_settings()
    fresh_ttl = int(settings.valuation_cache_ttl_seconds)
    stale_ttl = max(fresh_ttl, int(settings.valuation_stale_ttl_seconds))
    stored_at = monotonic()
    wall_time = datetime.now(timezone.utc)
    warnings = _unique((*fundamentals.warnings, *source_warnings))
    normalized = fundamentals.model_copy(update={"warnings": list(warnings)})
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
    with _CACHE_LOCK:
        _CACHE[key] = entry
    return envelope


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
            return fetch_yfinance_fundamentals(exchange, symbol), (warning,)

        primary = fetch_sec_fundamentals(exchange, symbol)
        if not _needs_yfinance_fallback(primary):
            return primary, ()
        try:
            fallback = fetch_yfinance_fundamentals(exchange, symbol)
        except YFinanceStatementsError as exc:
            warning = f"Optional yFinance fundamentals fallback failed: {exc}"
            return primary, (warning,)
        return _merge_sec_with_yfinance(primary, fallback)

    fundamentals = fetch_yfinance_fundamentals(exchange, symbol)
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
    top_level_updates["missing_fields"] = _remaining_missing_fields(candidate)
    top_level_updates["warnings"] = list(_unique(warnings))
    return primary.model_copy(update=top_level_updates), _unique(warnings)


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
    expected_unit = "SHARES" if field == "diluted_shares" else expected_currency
    return provenance_unit.strip().upper() == expected_unit


def _remaining_missing_fields(
    fundamentals: ValuationFundamentals,
) -> list[str]:
    remaining: list[str] = []
    for field in fundamentals.missing_fields:
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
    with _CACHE_LOCK:
        _CACHE.clear()
