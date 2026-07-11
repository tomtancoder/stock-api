import math
from datetime import date, datetime, timedelta, timezone
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from app.schemas import BankValuationDetails, ReitValuationDetails
from app.services import bank_valuation, valuation_router, valuation_service
from app.services.sec_companyfacts import SecCompanyFactsError
from app.services.tradingview_provider import TradingViewProviderError
from app.services.valuation_fundamentals import FundamentalsEnvelope
from app.services.valuation_router import CompanyClassification, ValuationUnreliable
from app.services.valuation_types import (
    FactProvenance,
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


def _bank_fundamentals() -> ValuationFundamentals:
    equities = [8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0]
    periods = []
    for index, equity in enumerate(equities):
        year = 2021 + index
        period_end = date(year, 12, 31)
        net_income = (
            None
            if index == 0
            else ((equities[index - 1] + equity) / 2.0) * 0.12
        )
        facts = {
            "common_equity": equity,
            "net_income_common": net_income,
            "common_dividends": (
                None if net_income is None else -(net_income * 0.40)
            ),
            "diluted_shares": 1_000.0,
            "total_assets": equity * 10.0,
        }
        periods.append(
            FinancialPeriod(
                period_end=period_end,
                fiscal_year=year,
                currency="SGD",
                sources={
                    field: FactProvenance(
                        provider="yfinance",
                        concept=field,
                        period_end=period_end,
                        unit=(
                            "shares" if field == "diluted_shares" else "SGD"
                        ),
                    )
                    for field, value in facts.items()
                    if value is not None
                },
                **facts,
            )
        )
    metrics = {
        "cet1_ratio": 0.14,
        "npl_ratio": 0.02,
        "loan_loss_coverage": 1.5,
        "regulatory_capital_headroom": 0.03,
    }
    return ValuationFundamentals(
        symbol="SGX:D05",
        exchange="SGX",
        currency="SGD",
        primary_source="yfinance_sgx",
        provider_security_type="EQUITY",
        sector="Financial Services",
        industry="Banks - Regional",
        issuer_classification="Commercial Banking",
        current_diluted_shares=1_000.0,
        bank_metrics=metrics,
        periods=periods,
        fetched_at=FETCHED_AT,
        sources={
            "financial_statements": "yfinance",
            "current_diluted_shares": "yfinance",
            "cet1_ratio": "yfinance_info",
            "npl_ratio": "yfinance_info",
            "loan_loss_coverage": "yfinance_info",
            "regulatory_capital_headroom": "yfinance_info",
        },
        warnings=["bank fundamentals warning"],
    )


def _reit_fundamentals(*, include_nav: bool = True) -> ValuationFundamentals:
    periods = []
    for index, dpu in enumerate((0.055, 0.058, 0.061, 0.063)):
        year = 2022 + index
        period_end = date(year, 12, 31)
        facts = {
            "distribution_per_unit": dpu,
            "nav_per_unit": 1.05 + (index * 0.02) if include_nav else None,
        }
        periods.append(
            FinancialPeriod(
                period_end=period_end,
                fiscal_year=year,
                currency="SGD",
                sources={
                    field: FactProvenance(
                        provider="yfinance",
                        concept=field,
                        period_end=period_end,
                        unit="SGD",
                    )
                    for field, value in facts.items()
                    if value is not None
                },
                **facts,
            )
        )
    return ValuationFundamentals(
        symbol="SGX:C38U",
        exchange="SGX",
        currency="SGD",
        primary_source="yfinance_sgx",
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Industrial",
        current_diluted_shares=1_000_000.0,
        reit_metrics={
            "aggregate_leverage": 0.34,
            "interest_coverage": 3.2,
            "occupancy": 0.98,
            "wale_years": 4.1,
        },
        periods=periods,
        fetched_at=FETCHED_AT,
        sources={
            "distribution_per_unit": "yfinance_dividends",
            "nav_per_unit": "yfinance_balance_sheet",
        },
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


def _thread_call(func, results, errors) -> None:
    try:
        results.append(func())
    except BaseException as exc:  # noqa: BLE001 - relay thread failures.
        errors.append(exc)


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


def test_service_normalizes_both_d05_forms_to_typed_bank_response(
    monkeypatch, fake_clock
):
    fundamentals = _bank_fundamentals()
    envelope = _envelope(
        fundamentals,
        warnings=(
            "SGX yFinance fundamentals cap valuation confidence at medium.",
        ),
    )
    fundamentals_calls = []
    quote_calls = []
    bank_calls = []

    def get_fundamentals(exchange: str, symbol: str) -> FundamentalsEnvelope:
        fundamentals_calls.append((exchange, symbol))
        return envelope

    def get_quote(exchange: str, symbol: str) -> dict[str, object]:
        quote_calls.append((exchange, symbol))
        return _quote(currency="SGD", price=9.0)

    def value_bank(candidate: ValuationFundamentals) -> ModelResult:
        bank_calls.append(candidate)
        return bank_valuation.value_bank(candidate)

    monkeypatch.setattr(
        valuation_service, "get_fundamentals", get_fundamentals
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider, "get_quote", get_quote
    )
    monkeypatch.setattr(
        valuation_router, "value_bank", value_bank, raising=False
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("a bank must never use owner earnings"),
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )

    bare = valuation_service.get_valuation("SGX", "D05")
    suffixed = valuation_service.get_valuation("SGX", "D05.SI")

    assert bare.model_dump() == suffixed.model_dump()
    assert bare.symbol == "SGX:D05"
    assert bare.exchange == "SGX"
    assert bare.currency == "SGD"
    assert bare.detected_company_type == "bank"
    assert bare.method == "bank_residual_income"
    assert bare.confidence == "medium"
    assert bare.intrinsic_value is not None
    values = (
        bare.intrinsic_value.bear,
        bare.intrinsic_value.base,
        bare.intrinsic_value.bull,
    )
    assert all(math.isfinite(value) and value > 0 for value in values)
    assert values[0] <= values[1] <= values[2]
    assert bare.model_details is not None
    assert isinstance(bare.model_details, BankValuationDetails)
    details = bare.model_details.model_dump()
    assert details["method"] == "bank_residual_income"
    assert details["normalized_roe"] == pytest.approx(0.12)
    assert details["book_value_per_share"] == 10.0
    assert details["payout_ratio"] == pytest.approx(0.40)
    assert details["usable_years"] == 4
    assert details["cet1_ratio"] == 0.14
    assert details["npl_ratio"] == 0.02
    assert details["loan_loss_coverage"] == 1.5
    assert "normalized_owner_earnings" not in details
    assert "owner_earnings_per_share" not in details
    assert "annual_history" not in details
    for metric in (
        "cet1_ratio",
        "npl_ratio",
        "loan_loss_coverage",
        "regulatory_capital_headroom",
    ):
        assert bare.sources[metric] == "yfinance_info"
    assert bare.warnings == [
        "bank fundamentals warning",
        "SGX yFinance fundamentals cap valuation confidence at medium.",
        "quote warning",
    ]
    assert fundamentals_calls == [("SGX", "D05"), ("SGX", "D05")]
    assert quote_calls == [("SGX", "D05")]
    assert bank_calls == [fundamentals]


def test_service_returns_typed_sgx_reit_distribution_nav_response(
    monkeypatch, fake_clock
):
    fundamentals = _reit_fundamentals()
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        classification=_classification("reit"),
        quote=_quote(price=0.93, currency="SGD"),
    )
    monkeypatch.setattr(
        valuation_service,
        "route_valuation",
        valuation_router.route_valuation,
    )

    response = valuation_service.get_valuation("SGX", "C38U.SI")

    assert response.symbol == "SGX:C38U"
    assert response.currency == "SGD"
    assert response.detected_company_type == "reit"
    assert response.method == "reit_distribution_nav"
    assert response.confidence == "medium"
    assert response.intrinsic_value is not None
    values = (
        response.intrinsic_value.bear,
        response.intrinsic_value.base,
        response.intrinsic_value.bull,
    )
    assert all(math.isfinite(value) and value > 0 for value in values)
    assert values[0] <= values[1] <= values[2]
    assert isinstance(response.model_details, ReitValuationDetails)
    assert response.model_details.nav_per_unit == pytest.approx(1.11)
    assert response.model_details.price_to_nav == pytest.approx(0.93 / 1.11)
    assert response.model_details.distribution_yield == pytest.approx(
        response.model_details.normalized_dpu / 0.93
    )
    assert response.model_details.aggregate_leverage == pytest.approx(0.34)
    assert response.sources["distribution_per_unit"] == "yfinance"


def test_service_reit_without_nav_returns_low_confidence_distribution_only(
    monkeypatch, fake_clock
):
    fundamentals = _reit_fundamentals(include_nav=False)
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        classification=_classification("reit"),
        quote=_quote(price=0.93, currency="SGD"),
    )
    monkeypatch.setattr(
        valuation_service,
        "route_valuation",
        valuation_router.route_valuation,
    )

    response = valuation_service.get_valuation("SGX", "C38U")

    assert response.method == "reit_distribution_only"
    assert response.confidence == "low"
    assert isinstance(response.model_details, ReitValuationDetails)
    assert response.model_details.nav_per_unit is None


def test_service_reit_with_incomplete_dpu_is_unreliable_without_fallback(
    monkeypatch, fake_clock
):
    fundamentals = _reit_fundamentals().model_copy(
        update={"periods": _reit_fundamentals().periods[:2]}, deep=True
    )
    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        classification=_classification("reit"),
        quote=_quote(price=0.93, currency="SGD"),
    )
    monkeypatch.setattr(
        valuation_service,
        "route_valuation",
        valuation_router.route_valuation,
    )

    response = valuation_service.get_valuation("SGX", "C38U")

    assert response.status == "valuation_unreliable"
    assert response.method is None
    assert response.intrinsic_value is None
    assert response.model_details is None
    assert any("three usable DPU years" in reason for reason in response.quality.reasons)


def test_reit_model_uses_cache_version_three():
    assert valuation_service.VALUATION_MODEL_VERSION == "3"


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


def test_expired_quote_refresh_failure_serves_stale_quote_until_stale_ttl(
    monkeypatch, fake_clock
):
    fundamentals = _fundamentals()
    quote_calls = 0

    _install_success(
        monkeypatch,
        envelope=_envelope(fundamentals),
        quote=_quote(currency="USD", price=6.0),
    )
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(
            valuation_quote_ttl_seconds=300,
            valuation_stale_ttl_seconds=900,
        ),
    )

    def quote(exchange: str, symbol: str) -> dict[str, object]:
        nonlocal quote_calls
        quote_calls += 1
        if quote_calls == 1:
            return _quote(currency="USD", price=6.0)
        raise TradingViewProviderError(
            "Quote provider is busy.", status_code=503
        )

    monkeypatch.setattr(
        valuation_service.tradingview_provider, "get_quote", quote
    )

    fresh = valuation_service.get_valuation("NASDAQ", "ACME")
    fake_clock.monotonic[0] = 300.0
    fake_clock.wall[0] = NOW + timedelta(seconds=300)
    stale = valuation_service.get_valuation("NASDAQ", "ACME")

    assert fresh.data_quality.stale is False
    assert stale.current_price == 6.0
    assert stale.data_quality.stale is True
    assert (
        "Quote refresh failed; serving stale cached data: "
        "Quote provider is busy."
    ) in stale.warnings
    assert quote_calls == 2

    fake_clock.monotonic[0] = 900.0
    fake_clock.wall[0] = NOW + timedelta(seconds=900)
    with pytest.raises(valuation_service.ValuationServiceError) as exc_info:
        valuation_service.get_valuation("NASDAQ", "ACME")

    assert exc_info.value.detail == "Quote provider is busy."
    assert quote_calls == 3


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
    assert len(valuation_service._MODEL_CACHE) == 1
    first_key = next(iter(valuation_service._MODEL_CACHE))
    assert first_key == (
        "NASDAQ:ACME",
        "3",
        FETCHED_AT.isoformat(),
    )
    changed = first_fundamentals.model_copy(
        update={"fetched_at": FETCHED_AT + timedelta(seconds=1)}, deep=True
    )
    envelope_box[0] = _envelope(changed)
    valuation_service.get_valuation("NASDAQ", "ACME")
    assert len(valuation_service._MODEL_CACHE) == 1
    changed_key = next(iter(valuation_service._MODEL_CACHE))
    assert changed_key == (
        "NASDAQ:ACME",
        "3",
        (FETCHED_AT + timedelta(seconds=1)).isoformat(),
    )
    monkeypatch.setattr(valuation_service, "VALUATION_MODEL_VERSION", "4")
    valuation_service.get_valuation("NASDAQ", "ACME")
    assert len(valuation_service._MODEL_CACHE) == 1
    version_key = next(iter(valuation_service._MODEL_CACHE))
    assert version_key == (
        "NASDAQ:ACME",
        "4",
        (FETCHED_AT + timedelta(seconds=1)).isoformat(),
    )
    assert valuation_service._MODEL_CURRENT_KEYS == {
        "NASDAQ:ACME": version_key
    }

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


@pytest.mark.parametrize("expired", [False, True])
def test_quote_cache_single_flight_shares_concurrent_miss_or_refresh(
    monkeypatch, fake_clock, expired
):
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )
    if expired:
        monkeypatch.setattr(
            valuation_service.tradingview_provider,
            "get_quote",
            lambda exchange, symbol: _quote(currency="USD", price=6.0),
        )
        valuation_service._get_quote(
            "NASDAQ", "ACME", "NASDAQ:ACME"
        )
        fake_clock.monotonic[0] = 300.0

    owner_started = Event()
    waiter_waiting = Event()
    release_owner = Event()
    duplicate_started = Event()
    inner_flight_event = Event()
    calls = 0
    calls_lock = Lock()
    results = []
    errors: list[BaseException] = []

    class TrackingFlightEvent:
        def wait(self, timeout: float | None = None) -> bool:
            waiter_waiting.set()
            return inner_flight_event.wait(timeout)

        def set(self) -> None:
            inner_flight_event.set()

    def blocking_quote(exchange: str, symbol: str):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            owner_started.set()
            assert release_owner.wait(2), "test did not release quote owner"
            return _quote(currency="USD", price=6.0)
        duplicate_started.set()
        return _quote(currency="USD", price=8.0)

    monkeypatch.setattr(
        valuation_service, "Event", lambda: TrackingFlightEvent(), raising=False
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        blocking_quote,
    )

    owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "ACME", "NASDAQ:ACME"
            ),
            results,
            errors,
        ),
    )
    waiter = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "acme", "NASDAQ:ACME"
            ),
            results,
            errors,
        ),
    )
    owner.start()
    assert owner_started.wait(2), "quote owner did not start"
    waiter.start()
    waiter_joined_flight = waiter_waiting.wait(0.5)
    release_owner.set()
    owner.join(2)
    waiter.join(2)

    assert not owner.is_alive()
    assert not waiter.is_alive()
    assert waiter_joined_flight is True
    assert errors == []
    assert duplicate_started.is_set() is False
    assert calls == 1
    assert [result["price"] for result in results] == [6.0, 6.0]
    assert valuation_service._QUOTE_IN_FLIGHT == {}


def test_quote_cache_failure_notifies_waiters_and_releases_flight(
    monkeypatch, fake_clock
):
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )
    owner_started = Event()
    waiter_waiting = Event()
    release_owner = Event()
    inner_flight_event = Event()
    calls = 0
    results = []
    errors: list[BaseException] = []

    class TrackingFlightEvent:
        def wait(self, timeout: float | None = None) -> bool:
            waiter_waiting.set()
            return inner_flight_event.wait(timeout)

        def set(self) -> None:
            inner_flight_event.set()

    def failing_quote(exchange: str, symbol: str):
        nonlocal calls
        calls += 1
        owner_started.set()
        assert release_owner.wait(2), "test did not release failing owner"
        raise TradingViewProviderError("quote failed", status_code=503)

    monkeypatch.setattr(
        valuation_service, "Event", lambda: TrackingFlightEvent(), raising=False
    )
    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        failing_quote,
    )

    owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "ACME", "NASDAQ:ACME"
            ),
            results,
            errors,
        ),
    )
    waiter = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "ACME", "NASDAQ:ACME"
            ),
            results,
            errors,
        ),
    )
    owner.start()
    assert owner_started.wait(2), "failing quote owner did not start"
    waiter.start()
    waiter_joined_flight = waiter_waiting.wait(0.5)
    release_owner.set()
    owner.join(2)
    waiter.join(2)

    assert not owner.is_alive()
    assert not waiter.is_alive()
    assert waiter_joined_flight is True
    assert results == []
    assert calls == 1
    assert len(errors) == 2
    assert all(
        isinstance(error, valuation_service.ValuationServiceError)
        and error.detail == "quote failed"
        for error in errors
    )
    assert valuation_service._QUOTE_IN_FLIGHT == {}
    assert valuation_service._QUOTE_CACHE == {}


def test_clear_during_quote_fetch_prevents_old_generation_repopulation(
    monkeypatch, fake_clock
):
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )
    owner_started = Event()
    release_owner = Event()
    results = []
    errors: list[BaseException] = []

    def blocking_quote(exchange: str, symbol: str):
        owner_started.set()
        assert release_owner.wait(2), "test did not release old quote owner"
        return _quote(currency="USD", price=6.0)

    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        blocking_quote,
    )
    owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "ACME", "NASDAQ:ACME"
            ),
            results,
            errors,
        ),
    )
    owner.start()
    assert owner_started.wait(2), "old quote owner did not start"

    valuation_service._clear_valuation_caches()
    release_owner.set()
    owner.join(2)

    assert not owner.is_alive()
    assert errors == []
    assert results[0]["price"] == 6.0
    assert valuation_service._QUOTE_CACHE == {}
    assert valuation_service._QUOTE_IN_FLIGHT == {}


def test_post_clear_quote_generation_wins_over_slow_old_owner(
    monkeypatch, fake_clock
):
    monkeypatch.setattr(
        valuation_service,
        "get_settings",
        lambda: SimpleNamespace(valuation_quote_ttl_seconds=300),
    )
    old_started = Event()
    release_old = Event()
    calls = 0
    calls_lock = Lock()
    old_results = []
    old_errors: list[BaseException] = []

    def ordered_quote(exchange: str, symbol: str):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            old_started.set()
            assert release_old.wait(2), "test did not release slow old quote"
            return _quote(currency="USD", price=6.0)
        if call_number == 2:
            return _quote(currency="USD", price=8.0)
        pytest.fail("post-clear quote result was not cached")

    monkeypatch.setattr(
        valuation_service.tradingview_provider,
        "get_quote",
        ordered_quote,
    )
    old_owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_quote(
                "NASDAQ", "ACME", "NASDAQ:ACME"
            ),
            old_results,
            old_errors,
        ),
    )
    old_owner.start()
    assert old_started.wait(2), "slow old quote did not start"

    valuation_service._clear_valuation_caches()
    new_result = valuation_service._get_quote(
        "NASDAQ", "ACME", "NASDAQ:ACME"
    )
    release_old.set()
    old_owner.join(2)
    cached_result = valuation_service._get_quote(
        "NASDAQ", "ACME", "NASDAQ:ACME"
    )

    assert not old_owner.is_alive()
    assert old_errors == []
    assert old_results[0]["price"] == 6.0
    assert new_result["price"] == 8.0
    assert cached_result["price"] == 8.0
    assert calls == 2
    assert valuation_service._QUOTE_IN_FLIGHT == {}


def test_clear_during_model_computation_prevents_old_generation_repopulation(
    monkeypatch, fake_clock
):
    fundamentals = _fundamentals()
    envelope = _envelope(fundamentals)
    owner_started = Event()
    release_owner = Event()
    results = []
    errors: list[BaseException] = []

    def blocking_model(candidate):
        owner_started.set()
        assert release_owner.wait(2), "test did not release old model owner"
        return _model_result()

    monkeypatch.setattr(valuation_service, "route_valuation", blocking_model)
    owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_model_result(
                "NASDAQ:ACME", fundamentals, envelope, NOW
            ),
            results,
            errors,
        ),
    )
    owner.start()
    assert owner_started.wait(2), "old model owner did not start"

    valuation_service._clear_valuation_caches()
    release_owner.set()
    owner.join(2)

    assert not owner.is_alive()
    assert errors == []
    assert results[0].base == 7.5
    assert valuation_service._MODEL_CACHE == {}
    assert valuation_service._MODEL_IN_FLIGHT == {}


def test_post_clear_model_generation_wins_over_slow_old_owner(
    monkeypatch, fake_clock
):
    fundamentals = _fundamentals()
    envelope = _envelope(fundamentals)
    old_result = _model_result()
    new_result = _model_result().model_copy(
        deep=True,
        update={"bear": 10.0, "base": 20.0, "bull": 30.0},
    )
    old_started = Event()
    release_old = Event()
    calls = 0
    calls_lock = Lock()
    old_results = []
    old_errors: list[BaseException] = []

    def ordered_model(candidate):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            old_started.set()
            assert release_old.wait(2), "test did not release slow old model"
            return old_result
        if call_number == 2:
            return new_result
        pytest.fail("post-clear model result was not cached")

    monkeypatch.setattr(valuation_service, "route_valuation", ordered_model)
    old_owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_model_result(
                "NASDAQ:ACME", fundamentals, envelope, NOW
            ),
            old_results,
            old_errors,
        ),
    )
    old_owner.start()
    assert old_started.wait(2), "slow old model did not start"

    valuation_service._clear_valuation_caches()
    post_clear = valuation_service._get_model_result(
        "NASDAQ:ACME", fundamentals, envelope, NOW
    )
    release_old.set()
    old_owner.join(2)
    cached = valuation_service._get_model_result(
        "NASDAQ:ACME", fundamentals, envelope, NOW
    )

    assert not old_owner.is_alive()
    assert old_errors == []
    assert old_results[0].base == 7.5
    assert post_clear.base == 20.0
    assert cached.base == 20.0
    assert calls == 2
    assert valuation_service._MODEL_IN_FLIGHT == {}


def test_repeated_fundamentals_changes_keep_one_model_entry_per_identity(
    monkeypatch, fake_clock
):
    calls = 0

    def route(candidate):
        nonlocal calls
        calls += 1
        return _model_result()

    monkeypatch.setattr(valuation_service, "route_valuation", route)

    for day in range(10):
        fetched_at = FETCHED_AT + timedelta(days=day)
        fundamentals = _fundamentals(fetched_at=fetched_at)
        valuation_service._get_model_result(
            "NASDAQ:ACME",
            fundamentals,
            _envelope(fundamentals),
            NOW,
        )

        expected_key = (
            "NASDAQ:ACME",
            "3",
            fetched_at.isoformat(),
        )
        assert list(valuation_service._MODEL_CACHE) == [expected_key]
        assert valuation_service._MODEL_CURRENT_KEYS == {
            "NASDAQ:ACME": expected_key
        }

    assert calls == 10


def test_model_cache_access_prunes_expired_unrelated_entries(
    monkeypatch, fake_clock
):
    monkeypatch.setattr(
        valuation_service,
        "route_valuation",
        lambda candidate: _model_result(),
    )
    old_fundamentals = _fundamentals(
        exchange="NYSE",
        symbol="NYSE:OLD",
    )
    old_envelope = _envelope(
        old_fundamentals,
        fresh_until=NOW + timedelta(seconds=1),
    )
    valuation_service._get_model_result(
        "NYSE:OLD", old_fundamentals, old_envelope, NOW
    )
    assert len(valuation_service._MODEL_CACHE) == 1

    fake_clock.wall[0] = NOW + timedelta(seconds=2)
    current_fundamentals = _fundamentals()
    valuation_service._get_model_result(
        "NASDAQ:ACME",
        current_fundamentals,
        _envelope(current_fundamentals),
        fake_clock.wall[0],
    )

    assert len(valuation_service._MODEL_CACHE) == 1
    current_key = next(iter(valuation_service._MODEL_CACHE))
    assert current_key[0] == "NASDAQ:ACME"
    assert "NYSE:OLD" not in valuation_service._MODEL_CURRENT_KEYS


def test_slow_old_model_sibling_cannot_replace_newer_current_key(
    monkeypatch, fake_clock
):
    old_fundamentals = _fundamentals()
    new_fundamentals = old_fundamentals.model_copy(
        deep=True,
        update={"fetched_at": FETCHED_AT + timedelta(seconds=1)},
    )
    old_result = _model_result()
    new_result = _model_result().model_copy(
        deep=True,
        update={"bear": 10.0, "base": 20.0, "bull": 30.0},
    )
    old_started = Event()
    release_old = Event()
    calls = 0
    calls_lock = Lock()
    old_results = []
    old_errors: list[BaseException] = []

    def ordered_model(candidate):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            old_started.set()
            assert release_old.wait(2), "test did not release old sibling"
            return old_result
        if call_number == 2:
            return new_result
        pytest.fail("newer sibling was not reused from cache")

    monkeypatch.setattr(valuation_service, "route_valuation", ordered_model)
    old_owner = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_model_result(
                "NASDAQ:ACME",
                old_fundamentals,
                _envelope(old_fundamentals),
                NOW,
            ),
            old_results,
            old_errors,
        ),
    )
    old_owner.start()
    assert old_started.wait(2), "old sibling did not start"

    newer = valuation_service._get_model_result(
        "NASDAQ:ACME",
        new_fundamentals,
        _envelope(new_fundamentals),
        NOW,
    )
    release_old.set()
    old_owner.join(2)
    cached = valuation_service._get_model_result(
        "NASDAQ:ACME",
        new_fundamentals,
        _envelope(new_fundamentals),
        NOW,
    )

    newer_key = (
        "NASDAQ:ACME",
        "3",
        (FETCHED_AT + timedelta(seconds=1)).isoformat(),
    )
    assert not old_owner.is_alive()
    assert old_errors == []
    assert old_results[0].base == 7.5
    assert newer.base == 20.0
    assert cached.base == 20.0
    assert list(valuation_service._MODEL_CACHE) == [newer_key]
    assert valuation_service._MODEL_CURRENT_KEYS == {
        "NASDAQ:ACME": newer_key
    }
    assert valuation_service._MODEL_IN_FLIGHT == {}
    assert calls == 2


def test_older_fundamentals_request_is_non_promoting_after_newer_cache(
    monkeypatch, fake_clock
):
    old_fundamentals = _fundamentals()
    newer_fetched_at = FETCHED_AT + timedelta(seconds=1)
    new_fundamentals = old_fundamentals.model_copy(
        deep=True,
        update={"fetched_at": newer_fetched_at},
    )
    old_result = _model_result()
    new_result = _model_result().model_copy(
        deep=True,
        update={"bear": 10.0, "base": 20.0, "bull": 30.0},
    )
    old_started = Event()
    release_old = Event()
    calls = 0
    old_results = []
    old_errors: list[BaseException] = []

    def ordered_model(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            return new_result
        if calls == 2:
            old_started.set()
            assert release_old.wait(2), "test did not release older request"
            return old_result
        return new_result

    monkeypatch.setattr(valuation_service, "route_valuation", ordered_model)
    initial = valuation_service._get_model_result(
        "NASDAQ:ACME",
        new_fundamentals,
        _envelope(new_fundamentals),
        NOW,
    )
    newer_key = (
        "NASDAQ:ACME",
        "3",
        newer_fetched_at.isoformat(),
    )
    old_caller = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_model_result(
                "NASDAQ:ACME",
                old_fundamentals,
                _envelope(old_fundamentals),
                NOW,
            ),
            old_results,
            old_errors,
        ),
    )
    old_caller.start()
    assert old_started.wait(2), "older request did not start"
    current_while_old_runs = dict(valuation_service._MODEL_CURRENT_KEYS)
    cached_while_old_runs = list(valuation_service._MODEL_CACHE)
    release_old.set()
    old_caller.join(2)
    subsequent = valuation_service._get_model_result(
        "NASDAQ:ACME",
        new_fundamentals,
        _envelope(new_fundamentals),
        NOW,
    )

    assert not old_caller.is_alive()
    assert old_errors == []
    assert initial.base == 20.0
    assert old_results[0].base == 7.5
    assert subsequent.base == 20.0
    assert current_while_old_runs == {"NASDAQ:ACME": newer_key}
    assert cached_while_old_runs == [newer_key]
    assert valuation_service._MODEL_CURRENT_KEYS == {
        "NASDAQ:ACME": newer_key
    }
    assert list(valuation_service._MODEL_CACHE) == [newer_key]
    assert valuation_service._MODEL_IN_FLIGHT == {}
    assert calls == 2


def test_old_model_version_completion_is_non_promoting(
    monkeypatch, fake_clock
):
    monkeypatch.setattr(valuation_service, "VALUATION_MODEL_VERSION", "2")
    fundamentals = _fundamentals()
    old_result = _model_result()
    new_result = _model_result().model_copy(
        deep=True,
        update={"bear": 10.0, "base": 20.0, "bull": 30.0},
    )
    old_started = Event()
    release_old = Event()
    calls = 0
    old_results = []
    old_errors: list[BaseException] = []

    def ordered_model(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            assert release_old.wait(2), "test did not release old version"
            return old_result
        return new_result

    monkeypatch.setattr(valuation_service, "route_valuation", ordered_model)
    old_caller = Thread(
        target=_thread_call,
        args=(
            lambda: valuation_service._get_model_result(
                "NASDAQ:ACME",
                fundamentals,
                _envelope(fundamentals),
                NOW,
            ),
            old_results,
            old_errors,
        ),
    )
    old_caller.start()
    assert old_started.wait(2), "old model version did not start"

    monkeypatch.setattr(valuation_service, "VALUATION_MODEL_VERSION", "3")
    release_old.set()
    old_caller.join(2)
    cache_after_old_completion = dict(valuation_service._MODEL_CACHE)
    current_after_old_completion = dict(
        valuation_service._MODEL_CURRENT_KEYS
    )
    current = valuation_service._get_model_result(
        "NASDAQ:ACME",
        fundamentals,
        _envelope(fundamentals),
        NOW,
    )

    current_key = (
        "NASDAQ:ACME",
        "3",
        FETCHED_AT.isoformat(),
    )
    assert not old_caller.is_alive()
    assert old_errors == []
    assert old_results[0].base == 7.5
    assert cache_after_old_completion == {}
    assert current_after_old_completion == {}
    assert current.base == 20.0
    assert list(valuation_service._MODEL_CACHE) == [current_key]
    assert valuation_service._MODEL_CURRENT_KEYS == {
        "NASDAQ:ACME": current_key
    }
    assert calls == 2
