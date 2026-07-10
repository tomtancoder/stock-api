from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Mapping

from app.core.config import get_settings
from app.schemas import ValuationResponse
from app.services import tradingview_provider
from app.services.market_symbols import normalize_exchange, to_public_symbol
from app.services.sec_companyfacts import SecCompanyFactsError
from app.services.valuation_fundamentals import (
    FundamentalsEnvelope,
    get_fundamentals,
)
from app.services.valuation_math import classify_price
from app.services.valuation_router import (
    CompanyClassification,
    ValuationUnreliable,
    classify_company,
    route_valuation,
)
from app.services.valuation_types import ModelResult, ValuationFundamentals
from app.services.yfinance_statements import YFinanceStatementsError


VALUATION_MODEL_VERSION = "1"


class ValuationServiceError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 502,
        retry_after_s: int | None = None,
        reasons: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.reasons = list(reasons) if reasons else [detail]
        self.headers = (
            {"Retry-After": str(retry_after_s)}
            if retry_after_s is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class _ModelCacheEntry:
    result: ModelResult
    fresh_until: datetime


@dataclass(frozen=True, slots=True)
class _QuoteCacheEntry:
    payload: dict[str, Any]
    expires_at: float


_ModelCacheKey = tuple[str, str, str]
_QuoteCacheKey = tuple[str, str]

_MODEL_CACHE: dict[_ModelCacheKey, _ModelCacheEntry] = {}
_QUOTE_CACHE: dict[_QuoteCacheKey, _QuoteCacheEntry] = {}
_CACHE_LOCK = Lock()


def get_valuation(exchange: str, symbol: str) -> ValuationResponse:
    venue = normalize_exchange(exchange)
    public_symbol = to_public_symbol(venue, symbol)
    provider_symbol = public_symbol.split(":", 1)[1]
    valuation_as_of = _utc_now()

    envelope = _get_fundamentals(venue, provider_symbol)
    fundamentals = envelope.fundamentals
    classification = classify_company(fundamentals)

    model_result: ModelResult | None = None
    unreliable_reasons: list[str] = []
    try:
        model_result = _get_model_result(
            public_symbol,
            fundamentals,
            envelope,
            valuation_as_of,
        )
    except ValuationUnreliable as exc:
        unreliable_reasons.extend(exc.reasons)
        unreliable_reasons.extend(classification.reasons)

    quote = _get_quote(venue, provider_symbol, public_symbol)
    current_price = _quote_price(quote)
    fundamentals_currency = _currency(
        fundamentals.currency, "Fundamentals currency is missing or invalid."
    )
    quote_currency = _currency(
        quote.get("currency"), "Quote currency is missing or invalid."
    )
    if quote_currency != fundamentals_currency:
        unreliable_reasons.append(
            f"Quote currency {quote_currency} does not match fundamentals "
            f"currency {fundamentals_currency}."
        )

    price_as_of, timestamp_warning = _price_as_of(
        quote.get("timestamp"), valuation_as_of
    )
    warnings = _warnings(
        fundamentals.warnings,
        envelope.warnings,
        model_result.warnings if model_result is not None else (),
        quote.get("warnings"),
        (timestamp_warning,) if timestamp_warning else (),
    )
    sources = _sources(fundamentals)
    missing_fields = list(fundamentals.missing_fields)

    if quote_currency != fundamentals_currency:
        missing_fields.append("currency_mismatch")

    common = {
        "symbol": public_symbol,
        "exchange": venue,
        "currency": fundamentals_currency,
        "detected_company_type": classification.company_type,
        "classification_sources": list(classification.sources),
        "current_price": current_price,
        "price_as_of": price_as_of,
        "data_quality": {
            "primary_source": fundamentals.primary_source,
            "financials_as_of": _financials_as_of(fundamentals),
            "valuation_as_of": valuation_as_of,
            "next_refresh_at": _as_utc(envelope.fresh_until),
            "stale": envelope.stale,
            "missing_fields": _unique(missing_fields),
        },
        "sources": sources,
        "warnings": warnings,
    }

    if unreliable_reasons or model_result is None:
        reasons = _unique(
            unreliable_reasons
            or ["Valuation model selection is unreliable."]
        )
        return ValuationResponse(
            **common,
            method=None,
            status="valuation_unreliable",
            confidence=None,
            intrinsic_value=None,
            model_details=None,
            quality={"eligible": False, "reasons": reasons, "details": {}},
            assumptions={},
        )

    price_classification = classify_price(
        current_price,
        bear=model_result.bear,
        base=model_result.base,
        bull=model_result.bull,
    )
    return ValuationResponse(
        **common,
        method=model_result.method,
        status=price_classification.status,
        confidence=_confidence(fundamentals, envelope, model_result),
        intrinsic_value={
            "bear": model_result.bear,
            "base": model_result.base,
            "bull": model_result.bull,
            "margin_of_safety_price": (
                price_classification.margin_of_safety_price
            ),
            "price_to_base_value": price_classification.price_to_base_value,
            "upside_downside_percent": (
                price_classification.upside_downside_percent
            ),
        },
        model_details=deepcopy(model_result.details),
        quality=deepcopy(model_result.quality),
        assumptions=deepcopy(model_result.assumptions),
    )


def _get_fundamentals(
    exchange: str, symbol: str
) -> FundamentalsEnvelope:
    try:
        return get_fundamentals(exchange, symbol)
    except SecCompanyFactsError as exc:
        status_code = 404 if exc.status_code == 404 else 502
        raise ValuationServiceError(
            str(exc), status_code=status_code, reasons=[str(exc)]
        ) from exc
    except YFinanceStatementsError as exc:
        raise ValuationServiceError(
            str(exc), status_code=502, reasons=[str(exc)]
        ) from exc


def _get_model_result(
    public_symbol: str,
    fundamentals: ValuationFundamentals,
    envelope: FundamentalsEnvelope,
    now: datetime,
) -> ModelResult:
    fetched_at = _as_utc(fundamentals.fetched_at)
    key = (
        public_symbol,
        VALUATION_MODEL_VERSION,
        fetched_at.isoformat(),
    )
    fresh_until = _as_utc(envelope.fresh_until)
    with _CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None and now < cached.fresh_until:
            return cached.result.model_copy(deep=True)

    result = route_valuation(fundamentals)
    stored = result.model_copy(deep=True)
    with _CACHE_LOCK:
        for stale_key in tuple(_MODEL_CACHE):
            if stale_key[0] == public_symbol and stale_key != key:
                del _MODEL_CACHE[stale_key]
        if now < fresh_until:
            _MODEL_CACHE[key] = _ModelCacheEntry(
                result=stored,
                fresh_until=fresh_until,
            )
    return result.model_copy(deep=True)


def _get_quote(
    exchange: str, symbol: str, public_symbol: str
) -> dict[str, Any]:
    key = (exchange, public_symbol)
    now = monotonic()
    with _CACHE_LOCK:
        cached = _QUOTE_CACHE.get(key)
        if cached is not None and now < cached.expires_at:
            return deepcopy(cached.payload)

    try:
        payload = tradingview_provider.get_quote(exchange, symbol)
    except tradingview_provider.TradingViewProviderError as exc:
        status_code = 404 if exc.status_code == 404 else 502
        raise ValuationServiceError(
            str(exc),
            status_code=status_code,
            retry_after_s=exc.retry_after_s,
            reasons=[str(exc)],
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValuationServiceError(
            "Quote provider returned an invalid response.",
            reasons=["Quote provider returned an invalid response."],
        )

    normalized = deepcopy(dict(payload))
    _quote_price(normalized)
    _currency(normalized.get("currency"), "Quote currency is missing or invalid.")
    ttl = int(get_settings().valuation_quote_ttl_seconds)
    with _CACHE_LOCK:
        _QUOTE_CACHE[key] = _QuoteCacheEntry(
            payload=deepcopy(normalized), expires_at=now + ttl
        )
    return normalized


def _quote_price(quote: Mapping[str, Any]) -> float:
    value = quote.get("price")
    if value is None or isinstance(value, bool):
        raise ValuationServiceError(
            "Current quote price is missing or invalid.",
            reasons=["Current quote price is missing or invalid."],
        )
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise ValuationServiceError(
            "Current quote price is missing or invalid.",
            reasons=["Current quote price is missing or invalid."],
        ) from exc
    if not math.isfinite(price) or price <= 0:
        raise ValuationServiceError(
            "Current quote price is missing or invalid.",
            reasons=["Current quote price is missing or invalid."],
        )
    return price


def _currency(value: Any, detail: str) -> str:
    if not isinstance(value, str):
        raise ValuationServiceError(detail, reasons=[detail])
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValuationServiceError(detail, reasons=[detail])
    return normalized


def _price_as_of(
    value: Any, fallback: datetime
) -> tuple[datetime, str | None]:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            parsed = None
    if parsed is None:
        return fallback, (
            "Quote timestamp was unavailable or invalid; valuation time was used."
        )
    return _as_utc(parsed), None


def _confidence(
    fundamentals: ValuationFundamentals,
    envelope: FundamentalsEnvelope,
    result: ModelResult,
) -> str:
    explicit = result.quality.get("confidence")
    quality_details = result.quality.get("details")
    if not isinstance(quality_details, Mapping):
        quality_details = {}
    quality_reasons = result.quality.get("reasons")
    has_quality_reasons = isinstance(quality_reasons, (list, tuple)) and bool(
        quality_reasons
    )
    if (
        envelope.stale
        or fundamentals.missing_fields
        or explicit == "low"
        or result.method == "reit_distribution_only"
        or has_quality_reasons
        or quality_details.get("material_optional_data_gaps") is True
        or quality_details.get("partial_result") is True
    ):
        return "low"

    usable_years = result.details.get("usable_years")
    if not isinstance(usable_years, int):
        usable_years = quality_details.get("usable_years")
    official_source = fundamentals.primary_source.strip().casefold() == (
        "sec_companyfacts"
    )
    source_values = (
        value.strip().casefold()
        for value in fundamentals.sources.values()
        if isinstance(value, str)
    )
    used_fallback = any("yfinance" in value for value in source_values)
    if (
        explicit != "medium"
        and official_source
        and usable_years is not None
        and usable_years >= 5
        and not used_fallback
    ):
        return "high"
    return "medium"


def _financials_as_of(fundamentals: ValuationFundamentals):
    return max(
        (period.period_end for period in fundamentals.periods), default=None
    )


def _sources(fundamentals: ValuationFundamentals) -> dict[str, str]:
    sources = dict(fundamentals.sources)
    for period in sorted(
        fundamentals.periods, key=lambda candidate: candidate.period_end
    ):
        for field, provenance in period.sources.items():
            sources[field] = provenance.provider
    sources["current_price"] = "existing_quote_provider"
    return sources


def _warnings(*groups: Any) -> list[str]:
    warnings: list[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            values = (group,)
        else:
            try:
                values = tuple(group)
            except TypeError:
                continue
        for value in values:
            if isinstance(value, str) and value and value not in warnings:
                warnings.append(value)
    return warnings


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(values))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clear_valuation_caches() -> None:
    with _CACHE_LOCK:
        _MODEL_CACHE.clear()
        _QUOTE_CACHE.clear()
