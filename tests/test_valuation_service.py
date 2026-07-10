from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import valuation_service
from app.services.sec_companyfacts import SecCompanyFactsError
from app.services.tradingview_provider import TradingViewProviderError
from app.services.valuation_fundamentals import FundamentalsEnvelope
from app.services.valuation_router import CompanyClassification, ValuationUnreliable
from app.services.valuation_types import (
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)
from app.services.yfinance_statements import YFinanceStatementsError


UTC = timezone.utc
NOW = datetime(2026, 7, 10, 10, 30, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)


def _period(year: int, *, currency: str = "USD") -> FinancialPeriod:
    return FinancialPeriod(
        period_end=date(year, 12, 31),
        fiscal_year=year,
        currency=currency,
        operating_cash_flow=1_400.0,
        capital_expenditure=-200.0,
        stock_based_compensation=100.0,
        interest_paid_outside_operating=0.0,
        revenue=5_000.0,
        diluted_shares=100.0,
    )


def _fundamentals(
    *,
    exchange: str = "NASDAQ",
    symbol: str = "NASDAQ:ACME",
    currency: str = "USD",
    primary_source: str = "sec_companyfacts",
    usable_years: int = 5,
    fetched_at: datetime = FETCHED_AT,
    missing_fields: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ValuationFundamentals:
    start_year = 2026 - usable_years
    return ValuationFundamentals(
        symbol=symbol,
        exchange=exchange,
        currency=currency,
        primary_source=primary_source,
        provider_security_type="EQUITY",
        sector="Technology",
        industry="Software",
        current_diluted_shares=100.0,
        periods=[
            _period(year, currency=currency)
            for year in range(start_year, 2026)
        ],
        fetched_at=fetched_at,
        sources={
            "financial_statements": primary_source,
            "operating_cash_flow": primary_source,
            "capital_expenditure": primary_source,
            "diluted_shares": primary_source,
        },
        missing_fields=missing_fields or [],
        warnings=warnings or [],
    )


def _envelope(
    fundamentals: ValuationFundamentals,
    *,
    fresh_until: datetime | None = None,
    stale: bool = False,
    warnings: tuple[str, ...] = (),
) -> FundamentalsEnvelope:
    return FundamentalsEnvelope(
        fundamentals=fundamentals,
        fresh_until=fresh_until or NOW + timedelta(hours=24),
        stale_until=NOW + timedelta(days=7),
        stale=stale,
        warnings=warnings,
    )


def _classification(
    company_type: str = "operating_company",
    *,
    supported: bool = True,
    reasons: tuple[str, ...] = (),
) -> CompanyClassification:
    return CompanyClassification(
        company_type=company_type,
        supported=supported,
        sources=("provider_industry", "statement_structure"),
        reasons=reasons,
    )


def _model_result(*, usable_years: int = 5) -> ModelResult:
    return ModelResult(
        method="owner_earnings_dcf",
        detected_company_type="operating_company",
        bear=5.8,
        base=7.5,
        bull=9.1,
        details={
            "method": "owner_earnings_dcf",
            "normalized_owner_earnings": 750.0,
            "owner_earnings_per_share": 7.5,
            "maintenance_capex_method": "total_capital_expenditure",
            "annual_history": [],
            "derived_growth": 0.04,
            "usable_years": usable_years,
        },
        assumptions={
            "projection_years": 10,
            "margin_of_safety": 0.25,
            "scenarios": {"base": {"required_return": 0.1}},
        },
        quality={
            "eligible": True,
            "reasons": [],
            "details": {"usable_years": usable_years},
        },
        warnings=["model warning"],
    )


def _quote(
    *,
    price: float | None = 7.15,
    currency: str | None = "SGD",
    timestamp: str | None = "2026-07-10T10:15:00Z",
) -> dict[str, object]:
    return {
        "symbol": "S63.SI",
        "exchange": "SGX",
        "price": price,
        "currency": currency,
        "source": "Yahoo Finance",
        "timestamp": timestamp,
        "warnings": ["quote warning"],
    }


@pytest.fixture(autouse=True)
def _reset_service_caches():
    valuation_service._clear_valuation_caches()
    yield
    valuation_service._clear_valuation_caches()


@pytest.fixture
def fake_clock(monkeypatch):
    clock = SimpleNamespace(wall=[NOW], monotonic=[0.0])
    monkeypatch.setattr(valuation_service, "_utc_now", lambda: clock.wall[0])
    monkeypatch.setattr(
        valuation_service, "monotonic", lambda: clock.monotonic[0]
    )
    return clock


def _install_success(
    monkeypatch,
    *,
    envelope: FundamentalsEnvelope,
    classification: CompanyClassification | None = None,
    model_result: ModelResult | None = None,
    quote: dict[str, object] | None = None,
) -> None:
    monkeypatch.setattr(
        valuation_service, "get_fundamentals", lambda exchange, symbol: envelope
    )
    monkeypatch.setattr(
        valuation_service,
        "classify_company",
        lambda fundamentals: classification or _classification(),
    )
    monkeypatch.setattr(
        valuation_service,
        "route_valuation",
        lambda fundamentals: model_result or _model_result(),
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        lambda exchange, symbol: quote or _quote(),
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )


def test_service_builds_typed_owner_earnings_response(monkeypatch, fake_clock):
    fundamentals = _fundamentals(
        exchange="SGX",
        symbol="SGX:S63",
        currency="sgd",
        primary_source="yfinance_sgx",
        warnings=["fundamentals warning"],
    )
    envelope = _envelope(
        fundamentals,
        warnings=("fundamentals warning", "facade warning"),
    )
    _install_success(
        monkeypatch,
        envelope=envelope,
        quote=_quote(currency=" sgd "),
    )

    response = valuation_service.get_valuation(" sgx ", "s63.si")

    assert response.symbol == "SGX:S63"
    assert response.exchange == "SGX"
    assert response.currency == "SGD"
    assert response.detected_company_type == "operating_company"
    assert response.method == "owner_earnings_dcf"
    assert response.classification_sources == [
        "provider_industry",
        "statement_structure",
    ]
    assert response.status == "fair"
    assert response.confidence == "medium"
    assert response.current_price == 7.15
    assert response.price_as_of == datetime(2026, 7, 10, 10, 15, tzinfo=UTC)
    assert response.intrinsic_value is not None
    assert response.intrinsic_value.model_dump() == {
        "bear": 5.8,
        "base": 7.5,
        "bull": 9.1,
        "margin_of_safety_price": 5.625,
        "price_to_base_value": 0.9533,
        "upside_downside_percent": 4.9,
    }
    assert response.model_details is not None
    assert response.model_details.normalized_owner_earnings == 750.0
    assert response.model_details.maintenance_capex_method == (
        "total_capital_expenditure"
    )
    assert response.assumptions["projection_years"] == 10
    assert response.quality.eligible is True
    assert response.data_quality.primary_source == "yfinance_sgx"
    assert response.data_quality.financials_as_of == date(2025, 12, 31)
    assert response.data_quality.valuation_as_of == NOW
    assert response.data_quality.next_refresh_at == envelope.fresh_until
    assert response.data_quality.stale is False
    assert response.sources["operating_cash_flow"] == "yfinance_sgx"
    assert response.sources["current_price"] == "existing_quote_provider"
    assert response.warnings == [
        "fundamentals warning",
        "facade warning",
        "model warning",
        "quote warning",
    ]


@pytest.mark.parametrize(
    (
        "primary_source",
        "usable_years",
        "missing_fields",
        "stale",
        "expected",
    ),
    [
        ("sec_companyfacts", 5, [], False, "high"),
        ("sec_companyfacts", 3, [], False, "medium"),
        ("yfinance_sgx", 5, [], False, "medium"),
        ("yfinance_fallback", 5, [], False, "medium"),
        ("sec_companyfacts", 5, ["common_dividends"], False, "low"),
        ("sec_companyfacts", 5, [], True, "low"),
    ],
)
def test_confidence_follows_source_history_and_quality_rules(
    monkeypatch,
    fake_clock,
    primary_source,
    usable_years,
    missing_fields,
    stale,
    expected,
):
    fundamentals = _fundamentals(
        primary_source=primary_source,
        usable_years=usable_years,
        missing_fields=missing_fields,
    )
    envelope = _envelope(
        fundamentals,
        stale=stale,
        warnings=("serving stale cached data",) if stale else (),
    )
    model_result = _model_result(usable_years=usable_years)
    _install_success(
        monkeypatch,
        envelope=envelope,
        model_result=model_result,
        quote=_quote(currency="USD", price=6.0),
    )

    response = valuation_service.get_valuation("NASDAQ", "ACME")

    assert response.confidence == expected
    assert response.data_quality.stale is stale


def test_currency_mismatch_returns_unreliable_without_model_claims(
    monkeypatch, fake_clock
):
    fundamentals = _fundamentals(
        exchange="SGX",
        symbol="SGX:S63",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        quote=_quote(currency="USD"),
    )

    response = valuation_service.get_valuation("SGX", "S63")

    assert response.status == "valuation_unreliable"
    assert response.method is None
    assert response.confidence is None
    assert response.intrinsic_value is None
    assert response.model_details is None
    assert response.quality.eligible is False
    assert response.quality.reasons == [
        "Quote currency USD does not match fundamentals currency SGD."
    ]
    assert "currency_mismatch" in response.data_quality.missing_fields


@pytest.mark.parametrize(
    ("company_type", "reason"),
    [
        ("bank", "Bank valuation is recognized but not supported yet."),
        ("reit", "REIT valuation is recognized but not supported yet."),
    ],
)
def test_recognized_unsupported_models_return_valid_unreliable_response(
    monkeypatch, fake_clock, company_type, reason
):
    fundamentals = _fundamentals()
    classification = _classification(
        company_type,
        supported=False,
        reasons=(reason,),
    )
    route_calls = 0

    def reject_model(candidate):
        nonlocal route_calls
        route_calls += 1
        raise ValuationUnreliable([reason])

    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        classification=classification,
        quote=_quote(currency="USD", price=6.0),
    )
    monkeypatch.setattr(valuation_service, "route_valuation", reject_model)

    response = valuation_service.get_valuation("NASDAQ", "ACME")

    assert route_calls == 1
    assert response.status == "valuation_unreliable"
    assert response.detected_company_type == company_type
    assert response.method is None
    assert response.current_price == 6.0
    assert response.quality.model_dump() == {
        "eligible": False,
        "reasons": [reason],
        "details": {},
    }
    assert response.data_quality.financials_as_of == date(2025, 12, 31)


def test_stale_fundamentals_keep_numeric_result_with_low_confidence(
    monkeypatch, fake_clock
):
    warning = "Fundamentals refresh failed; serving stale cached data."
    fundamentals = _fundamentals()
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals, stale=True, warnings=(warning,)),
        quote=_quote(currency="USD", price=6.0),
    )

    response = valuation_service.get_valuation("NASDAQ", "ACME")

    assert response.status == "fair"
    assert response.intrinsic_value is not None
    assert response.confidence == "low"
    assert response.data_quality.stale is True
    assert warning in response.warnings


@pytest.mark.parametrize("price", [None, 0.0, -1.0, float("nan")])
def test_missing_or_invalid_quote_price_is_typed_provider_failure(
    monkeypatch, fake_clock, price
):
    fundamentals = _fundamentals()
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        quote=_quote(currency="USD", price=price),
    )

    with pytest.raises(valuation_service.ValuationServiceError) as exc_info:
        valuation_service.get_valuation("NASDAQ", "ACME")

    assert exc_info.value.status_code == 502
    assert exc_info.value.retry_after_s is None
    assert exc_info.value.reasons == ["Current quote price is missing or invalid."]


@pytest.mark.parametrize(
    ("provider_error", "expected_status"),
    [
        (SecCompanyFactsError("Unknown ticker", status_code=404), 404),
        (SecCompanyFactsError("SEC unavailable"), 502),
        (YFinanceStatementsError("Yahoo unavailable"), 502),
    ],
)
def test_fundamentals_provider_failures_become_typed_service_errors(
    monkeypatch, fake_clock, provider_error, expected_status
):
    monkeypatch.setattr(
        valuation_service,
        "get_fundamentals",
        lambda exchange, symbol: (_ for _ in ()).throw(provider_error),
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        lambda exchange, symbol: pytest.fail("quote must not be requested"),
    )

    with pytest.raises(valuation_service.ValuationServiceError) as exc_info:
        valuation_service.get_valuation("NASDAQ", "ACME")

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == str(provider_error)
    assert exc_info.value.reasons == [str(provider_error)]


def test_quote_provider_failure_preserves_retry_hint_in_service_error(
    monkeypatch, fake_clock
):
    fundamentals = _fundamentals()
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        quote=_quote(currency="USD"),
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        lambda exchange, symbol: (_ for _ in ()).throw(
            TradingViewProviderError(
                "Quote provider is busy.",
                status_code=503,
                retry_after_s=60,
            )
        ),
    )

    with pytest.raises(valuation_service.ValuationServiceError) as exc_info:
        valuation_service.get_valuation("NASDAQ", "ACME")

    assert exc_info.value.status_code == 502
    assert exc_info.value.retry_after_s == 60
    assert exc_info.value.headers == {"Retry-After": "60"}
    assert exc_info.value.reasons == ["Quote provider is busy."]


def test_model_and_quote_caches_expire_independently(monkeypatch, fake_clock):
    fundamentals = _fundamentals()
    envelope = _envelope(fundamentals, fresh_until=NOW + timedelta(hours=24))
    model_calls = 0
    quote_calls = 0

    monkeypatch.setattr(
        valuation_service, "get_fundamentals", lambda exchange, symbol: envelope
    )
    monkeypatch.setattr(
        valuation_service,
        "classify_company",
        lambda candidate: _classification(),
    )

    def route(candidate):
        nonlocal model_calls
        model_calls += 1
        return _model_result()

    def quote(exchange, symbol):
        nonlocal quote_calls
        quote_calls += 1
        return _quote(
            currency="USD",
            price=70.0 if quote_calls == 1 else 80.0,
        )

    monkeypatch.setattr(valuation_service, "route_valuation", route)
    monkeypatch.setattr(
        valuation_service.tradingview_provider, "get_quote", quote
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )

    first = valuation_service.get_valuation("nasdaq", "acme")
    fake_clock.monotonic[0] = 299.0
    fake_clock.wall[0] = NOW + timedelta(seconds=299)
    second = valuation_service.get_valuation("NASDAQ", "ACME")
    fake_clock.monotonic[0] = 300.0
    fake_clock.wall[0] = NOW + timedelta(seconds=300)
    third = valuation_service.get_valuation("NASDAQ", "ACME")

    assert [first.current_price, second.current_price, third.current_price] == [
        70.0,
        70.0,
        80.0,
    ]
    assert [first.status, second.status, third.status] == [
        "very_expensive",
        "very_expensive",
        "very_expensive",
    ]
    assert model_calls == 1
    assert quote_calls == 2

    fake_clock.monotonic[0] = 86_400.0
    fake_clock.wall[0] = NOW + timedelta(hours=24)
    valuation_service.get_valuation("NASDAQ", "ACME")

    assert model_calls == 2
    assert quote_calls == 3


def test_fundamentals_timestamp_and_model_version_only_invalidate_model_cache(
    monkeypatch, fake_clock
):
    first_fundamentals = _fundamentals()
    envelope_box = [_envelope(first_fundamentals)]
    model_calls = 0
    quote_calls = 0

    monkeypatch.setattr(
        valuation_service,
        "get_fundamentals",
        lambda exchange, symbol: envelope_box[0],
    )
    monkeypatch.setattr(
        valuation_service,
        "classify_company",
        lambda candidate: _classification(),
    )

    def route(candidate):
        nonlocal model_calls
        model_calls += 1
        return _model_result()

    def quote(exchange, symbol):
        nonlocal quote_calls
        quote_calls += 1
        return _quote(currency="USD", price=6.0)

    monkeypatch.setattr(valuation_service, "route_valuation", route)
    monkeypatch.setattr(
        valuation_service.tradingview_provider, "get_quote", quote
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )

    valuation_service.get_valuation("NASDAQ", "ACME")
    changed = first_fundamentals.model_copy(
        update={"fetched_at": FETCHED_AT + timedelta(seconds=1)}, deep=True
    )
    envelope_box[0] = _envelope(changed)
    valuation_service.get_valuation("NASDAQ", "ACME")
    monkeypatch.setattr(valuation_service, "VALUATION_MODEL_VERSION", "2")
    valuation_service.get_valuation("NASDAQ", "ACME")

    assert model_calls == 3
    assert quote_calls == 1


def test_clear_valuation_caches_invalidates_both_layers(monkeypatch, fake_clock):
    fundamentals = _fundamentals()
    model_calls = 0
    quote_calls = 0

    monkeypatch.setattr(
        valuation_service,
        "get_fundamentals",
        lambda exchange, symbol: _envelope(fundamentals),
    )
    monkeypatch.setattr(
        valuation_service,
        "classify_company",
        lambda candidate: _classification(),
    )

    def route(candidate):
        nonlocal model_calls
        model_calls += 1
        return _model_result()

    def quote(exchange, symbol):
        nonlocal quote_calls
        quote_calls += 1
        return _quote(currency="USD", price=6.0)

    monkeypatch.setattr(valuation_service, "route_valuation", route)
    monkeypatch.setattr(
        valuation_service.tradingview_provider, "get_quote", quote
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )

    valuation_service.get_valuation("NASDAQ", "ACME")
    valuation_service._clear_valuation_caches()
    valuation_service.get_valuation("NASDAQ", "ACME")

    assert model_calls == 2
    assert quote_calls == 2
