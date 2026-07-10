from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from app.services import valuation_router
from app.services.valuation_types import (
    FactProvenance,
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)


def _period(
    year: int,
    *,
    currency: str = "USD",
    is_ttm: bool = False,
    **facts: float | None,
) -> FinancialPeriod:
    period_end = date(year, 12, 31)
    sources = {
        field: FactProvenance(
            provider="test_provider",
            concept=field,
            period_end=period_end,
            unit="shares" if field == "diluted_shares" else currency,
        )
        for field, value in facts.items()
        if value is not None
    }
    return FinancialPeriod(
        period_end=period_end,
        fiscal_year=None if is_ttm else year,
        is_ttm=is_ttm,
        currency=currency,
        sources=sources,
        **facts,
    )


def _fundamentals(
    *,
    provider_security_type: str | None = "EQUITY",
    sector: str | None = "Technology",
    industry: str | None = "Software",
    issuer_classification: str | None = None,
    periods: list[FinancialPeriod] | None = None,
    currency: str = "USD",
) -> ValuationFundamentals:
    return ValuationFundamentals(
        symbol="NASDAQ:ACME",
        exchange="NASDAQ",
        currency=currency,
        primary_source="sec_companyfacts",
        provider_security_type=provider_security_type,
        sector=sector,
        industry=industry,
        issuer_classification=issuer_classification,
        current_diluted_shares=100.0,
        periods=periods or [],
        fetched_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )


def _operating_period(year: int, *, is_ttm: bool = False) -> FinancialPeriod:
    scale = 1.0 + (year - 2021) * 0.04
    return _period(
        year,
        is_ttm=is_ttm,
        operating_cash_flow=1400.0 * scale,
        capital_expenditure=-200.0 * scale,
        stock_based_compensation=100.0 * scale,
        interest_paid_outside_operating=0.0,
        revenue=5000.0 * scale,
        diluted_shares=100.0,
    )


def _operating_company() -> ValuationFundamentals:
    return _fundamentals(
        periods=[
            *[_operating_period(year) for year in range(2021, 2026)],
            _operating_period(2026, is_ttm=True),
        ]
    )


def _bank_period(year: int) -> FinancialPeriod:
    return _period(
        year,
        net_income_common=100.0,
        common_equity=1000.0,
        total_assets=10_000.0,
        diluted_shares=100.0,
        common_dividends=40.0,
    )


def test_operating_company_classifies_and_routes_to_owner_earnings(monkeypatch):
    fundamentals = _operating_company()
    expected = ModelResult(
        method="owner_earnings_dcf",
        detected_company_type="operating_company",
        bear=10.0,
        base=20.0,
        bull=30.0,
        details={},
        assumptions={},
        quality={"eligible": True, "reasons": []},
    )
    calls: list[ValuationFundamentals] = []

    def fake_value(candidate: ValuationFundamentals) -> ModelResult:
        calls.append(candidate)
        return expected

    monkeypatch.setattr(valuation_router, "value_owner_earnings", fake_value)

    classification = valuation_router.classify_company(fundamentals)
    result = valuation_router.route_valuation(fundamentals)

    assert classification.company_type == "operating_company"
    assert classification.supported is True
    assert "statement_structure" in classification.sources
    assert result == expected
    assert calls == [fundamentals]


def test_bank_requires_industry_metadata_and_bank_like_statements(monkeypatch):
    fundamentals = _fundamentals(
        sector="Financial Services",
        industry="Banks - Regional",
        issuer_classification="Commercial Banking",
        periods=[_bank_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("bank must not use owner earnings"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "bank"
    assert classification.supported is False
    assert "industry" in classification.sources
    assert "statement_structure" in classification.sources
    with pytest.raises(valuation_router.ValuationUnreliable) as exc_info:
        valuation_router.route_valuation(fundamentals)
    assert exc_info.value.reasons == list(classification.reasons)


def test_bank_industry_detection_does_not_depend_on_other_metadata_fields():
    fundamentals = _fundamentals(
        provider_security_type=None,
        sector=None,
        industry="Commercial Banking",
        periods=[_bank_period(year) for year in range(2023, 2026)],
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "bank"
    assert classification.sources == ("industry", "statement_structure")


def test_bank_metadata_without_bank_statements_is_ambiguous_and_never_falls_back(
    monkeypatch,
):
    fundamentals = _fundamentals(
        sector="Financial Services",
        industry="Banks - Regional",
        periods=[_operating_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail(
            "bank-like metadata must not fall back to owner earnings"
        ),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "ambiguous"
    assert classification.supported is False
    assert any("bank" in reason.lower() for reason in classification.reasons)
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_explicit_reit_type_takes_precedence_over_bank_evidence(monkeypatch):
    fundamentals = _fundamentals(
        provider_security_type="REIT",
        sector="Financial Services",
        industry="Banks - Regional",
        periods=[_bank_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("REIT must not use owner earnings"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "reit"
    assert classification.supported is False
    assert classification.sources[0] == "provider_security_type"
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_explicit_unsupported_type_precedes_reit_industry_metadata(monkeypatch):
    etf = _fundamentals(
        provider_security_type="ETF",
        sector="Real Estate",
        industry="REIT - Retail",
        periods=[_operating_period(year) for year in range(2023, 2026)],
    )
    equity_reit = etf.model_copy(
        update={"provider_security_type": "EQUITY"}, deep=True
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("fund or REIT must not use owner earnings"),
    )

    etf_classification = valuation_router.classify_company(etf)
    reit_classification = valuation_router.classify_company(equity_reit)

    assert etf_classification.company_type == "unsupported"
    assert etf_classification.sources == ("provider_security_type",)
    assert reit_classification.company_type == "reit"
    assert "industry" in reit_classification.sources
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(etf)


def test_insurer_is_unsupported(monkeypatch):
    fundamentals = _fundamentals(
        sector="Financial Services",
        industry="Insurance - Property & Casualty",
        periods=[_bank_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("insurer must not use owner earnings"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "unsupported"
    assert classification.supported is False
    assert any("insurance" in reason.lower() for reason in classification.reasons)
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_compact_mutual_fund_security_type_is_unsupported(monkeypatch):
    fundamentals = _fundamentals(
        provider_security_type="MUTUALFUND",
        periods=[_operating_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("fund must not use owner earnings"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "unsupported"
    assert classification.sources == ("provider_security_type",)
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_conflicting_bank_and_operating_metadata_is_ambiguous(monkeypatch):
    fundamentals = _fundamentals(
        sector="Technology",
        industry="Banks - Regional",
        periods=[_bank_period(year) for year in range(2023, 2026)],
    )
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("ambiguous company must not be valued"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "ambiguous"
    assert classification.supported is False
    assert set(classification.sources) >= {"sector", "industry"}
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_operating_classification_rejects_incompatible_fact_units(monkeypatch):
    period = _operating_period(2025)
    invalid_revenue_source = period.sources["revenue"].model_copy(
        update={"unit": "shares"}
    )
    period = period.model_copy(
        update={
            "sources": {
                **period.sources,
                "revenue": invalid_revenue_source,
            }
        }
    )
    fundamentals = _fundamentals(periods=[period])
    monkeypatch.setattr(
        valuation_router,
        "value_owner_earnings",
        lambda candidate: pytest.fail("incompatible facts must not be valued"),
    )

    classification = valuation_router.classify_company(fundamentals)

    assert classification.company_type == "unsupported"
    assert classification.supported is False
    assert any("compatible" in reason.lower() for reason in classification.reasons)
    with pytest.raises(valuation_router.ValuationUnreliable):
        valuation_router.route_valuation(fundamentals)


def test_owner_earnings_input_failure_becomes_typed_unreliable():
    fundamentals = _fundamentals(periods=[_operating_period(2025)])

    with pytest.raises(valuation_router.ValuationUnreliable) as exc_info:
        valuation_router.route_valuation(fundamentals)

    assert isinstance(exc_info.value.reasons, list)
    assert any("three" in reason.lower() for reason in exc_info.value.reasons)


def test_classification_is_immutable():
    classification = valuation_router.classify_company(_operating_company())

    with pytest.raises(FrozenInstanceError):
        classification.supported = False
    assert isinstance(classification.sources, tuple)
    assert isinstance(classification.reasons, tuple)
