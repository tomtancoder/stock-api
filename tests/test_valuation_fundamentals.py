from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from app.services import valuation_fundamentals, valuation_service
from app.services.sec_companyfacts import SecCompanyFactsError
from app.services.valuation_types import (
    FactProvenance,
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)
from app.services.yfinance_statements import YFinanceStatementsError


def _period(
    period_end: date,
    *,
    currency: str = "USD",
    provider: str = "sec_companyfacts",
    **facts: float | None,
) -> FinancialPeriod:
    sources = {
        field: FactProvenance(
            provider=provider,
            concept=field,
            period_end=period_end,
            unit=(
                "shares"
                if field == "diluted_shares"
                else (
                    f"{currency}/unit"
                    if field in {"distribution_per_unit", "nav_per_unit"}
                    else currency
                )
            ),
        )
        for field, value in facts.items()
        if value is not None
    }
    return FinancialPeriod(
        period_end=period_end,
        fiscal_year=period_end.year,
        currency=currency,
        sources=sources,
        **facts,
    )


def _fundamentals(
    *,
    exchange: str = "NASDAQ",
    symbol: str = "NASDAQ:ACME",
    currency: str = "USD",
    primary_source: str = "sec_companyfacts",
    periods: list[FinancialPeriod] | None = None,
    missing_fields: list[str] | None = None,
    **updates: object,
) -> ValuationFundamentals:
    values: dict[str, object] = {
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency,
        "primary_source": primary_source,
        "provider_security_type": "EQUITY",
        "sector": "Technology",
        "industry": "Software",
        "current_diluted_shares": 100.0,
        "periods": periods or [],
        "fetched_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        "sources": {"financial_statements": primary_source},
        "missing_fields": missing_fields or [],
        "warnings": [],
    }
    values.update(updates)
    return ValuationFundamentals(**values)


def _settings(
    *,
    sec_user_agent: str | None = "stock-api test@example.com",
    fresh_ttl: int = 60,
    stale_ttl: int = 300,
) -> SimpleNamespace:
    return SimpleNamespace(
        sec_user_agent=sec_user_agent,
        valuation_cache_ttl_seconds=fresh_ttl,
        valuation_stale_ttl_seconds=stale_ttl,
    )


def test_reit_metrics_accept_only_the_approved_finite_keys():
    approved = {
        "aggregate_leverage": 0.38,
        "interest_coverage": 4.2,
        "occupancy": 0.97,
        "wale_years": 3.8,
        "recurring_property_capex": 12.0,
        "material_currency_exposure": 0.15,
    }

    fundamentals = _fundamentals(reit_metrics=approved)

    assert fundamentals.reit_metrics == approved


def test_reit_metrics_reject_unapproved_keys():
    with pytest.raises(ValueError, match="unsupported REIT metric keys"):
        _fundamentals(reit_metrics={"ffo": 100.0})


def test_reit_metrics_reject_non_finite_values():
    with pytest.raises(ValueError, match="REIT metrics must be finite"):
        _fundamentals(reit_metrics={"occupancy": float("nan")})


@pytest.mark.parametrize("value", [True, "0.97", object()])
def test_reit_metrics_reject_non_real_or_boolean_values_before_coercion(value):
    with pytest.raises(
        ValueError,
        match="REIT metric values must be real numbers and not booleans",
    ):
        _fundamentals(reit_metrics={"occupancy": value})


@pytest.fixture(autouse=True)
def _reset_cache():
    valuation_fundamentals._clear_cache()
    yield
    valuation_fundamentals._clear_cache()


def test_sec_primary_keeps_existing_facts_and_fills_missing_compatible_facts(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        sector=None,
        current_diluted_shares=None,
        missing_fields=["capital_expenditure", "current_diluted_shares"],
        periods=[
            _period(
                period_end,
                operating_cash_flow=100.0,
                revenue=300.0,
                capital_expenditure=None,
            )
        ],
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        periods=[
            _period(
                period_end,
                provider="yfinance",
                operating_cash_flow=999.0,
                revenue=999.0,
                capital_expenditure=-20.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "current_diluted_shares": "yfinance",
        },
    )
    calls: list[str] = []

    def fetch_sec(exchange: str, symbol: str) -> ValuationFundamentals:
        assert not valuation_fundamentals._CACHE_LOCK.locked()
        calls.append("sec")
        return sec

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        assert not valuation_fundamentals._CACHE_LOCK.locked()
        calls.append("yfinance")
        return yahoo

    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_sec_fundamentals", fetch_sec
    )
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    envelope = valuation_fundamentals.get_fundamentals("nasdaq", "acme")

    assert calls == ["sec", "yfinance"]
    assert envelope.fundamentals.primary_source == "sec_companyfacts"
    assert envelope.fundamentals.sector == "Technology"
    assert envelope.fundamentals.current_diluted_shares == 100.0
    merged = envelope.fundamentals.periods[0]
    assert merged.operating_cash_flow == 100.0
    assert merged.sources["operating_cash_flow"].provider == "sec_companyfacts"
    assert merged.capital_expenditure == -20.0
    assert merged.sources["capital_expenditure"].provider == "yfinance"
    assert "capital_expenditure" not in envelope.fundamentals.missing_fields
    assert envelope.stale is False


def test_sec_facade_merges_only_explicit_approved_bank_metrics_with_sources(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        bank_metrics={"cet1_ratio": 0.14},
        missing_fields=["operating_cash_flow"],
        periods=[_period(period_end, common_equity=10_000.0)],
        sources={
            "financial_statements": "sec_companyfacts",
            "cet1_ratio": "sec_companyfacts",
        },
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        bank_metrics={
            "cet1_ratio": 0.99,
            "npl_ratio": 0.02,
            "loan_loss_coverage": 1.5,
            "regulatory_capital_headroom": 0.03,
        },
        periods=[
            _period(
                period_end,
                provider="yfinance",
                operating_cash_flow=100.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "cet1_ratio": "yfinance_cet1",
            "npl_ratio": "yfinance_npl",
            "loan_loss_coverage": "yfinance_coverage",
            "regulatory_capital_headroom": "yfinance_headroom",
        },
    )
    yahoo.bank_metrics["tier1_ratio"] = 0.16
    yahoo.sources["tier1_ratio"] = "yfinance_unapproved"
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "NASDAQ", "BANK"
    ).fundamentals

    assert merged.bank_metrics == {
        "cet1_ratio": 0.14,
        "npl_ratio": 0.02,
        "loan_loss_coverage": 1.5,
        "regulatory_capital_headroom": 0.03,
    }
    assert merged.sources["cet1_ratio"] == "sec_companyfacts"
    assert merged.sources["npl_ratio"] == "yfinance_npl"
    assert merged.sources["loan_loss_coverage"] == "yfinance_coverage"
    assert (
        merged.sources["regulatory_capital_headroom"]
        == "yfinance_headroom"
    )
    assert "tier1_ratio" not in merged.bank_metrics
    assert "tier1_ratio" not in merged.sources


def test_sec_facade_does_not_merge_bank_metrics_across_currencies(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        missing_fields=["operating_cash_flow"],
        periods=[_period(period_end, common_equity=10_000.0)],
    )
    yahoo = _fundamentals(
        currency="SGD",
        primary_source="yfinance_fallback",
        bank_metrics={"cet1_ratio": 0.14, "npl_ratio": 0.02},
        periods=[
            _period(
                period_end,
                currency="SGD",
                provider="yfinance",
                total_assets=100_000.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "cet1_ratio": "yfinance_info",
            "npl_ratio": "yfinance_info",
        },
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "NASDAQ", "BANK"
    ).fundamentals

    assert merged.bank_metrics == {}
    assert "cet1_ratio" not in merged.sources
    assert "npl_ratio" not in merged.sources


def test_sec_facade_never_infers_bank_metrics_from_balance_sheet_rows(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        missing_fields=["operating_cash_flow"],
        periods=[_period(period_end, common_equity=10_000.0)],
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        periods=[
            _period(
                period_end,
                provider="yfinance",
                total_assets=100_000.0,
                total_debt=80_000.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "cet1_ratio": "unrelated_provider_metadata",
            "npl_ratio": "unrelated_provider_metadata",
        },
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "NASDAQ", "BANK"
    ).fundamentals

    assert merged.bank_metrics == {}
    assert "cet1_ratio" not in merged.sources
    assert "npl_ratio" not in merged.sources


def test_sec_facade_prefers_direct_reit_facts_and_merges_metrics_without_overwrite(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        reit_metrics={"aggregate_leverage": 0.40},
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[
            _period(
                period_end,
                common_dividends=120.0,
                common_equity=1_600.0,
                diluted_shares=1_000.0,
            )
        ],
        sources={
            "financial_statements": "sec_companyfacts",
            "aggregate_leverage": "sec_companyfacts",
        },
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        reit_metrics={
            "aggregate_leverage": 0.99,
            "occupancy": 0.97,
        },
        periods=[
            _period(
                period_end,
                provider="yfinance",
                distribution_per_unit=0.13,
                nav_per_unit=1.65,
                real_estate_depreciation=85.0,
                gain_on_property_sales=12.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "aggregate_leverage": "yfinance_info.aggregateLeverage",
            "occupancy": "yfinance_info.occupancy",
        },
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "NYSE", "REIT"
    ).fundamentals

    period = merged.periods[0]
    assert period.distribution_per_unit == 0.13
    assert period.nav_per_unit == 1.65
    assert period.sources["distribution_per_unit"].provider == "yfinance"
    assert period.sources["nav_per_unit"].provider == "yfinance"
    assert period.real_estate_depreciation == 85.0
    assert period.gain_on_property_sales == 12.0
    assert merged.reit_metrics == {
        "aggregate_leverage": 0.40,
        "occupancy": 0.97,
    }
    assert merged.sources["aggregate_leverage"] == "sec_companyfacts"
    assert merged.sources["occupancy"] == "yfinance_info.occupancy"
    assert "distribution_per_unit" not in merged.missing_fields
    assert "nav_per_unit" not in merged.missing_fields


def test_sec_facade_derives_reit_dpu_and_nav_only_from_compatible_raw_facts(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[
            _period(
                period_end,
                common_dividends=120.0,
                common_equity=1_600.0,
                diluted_shares=1_000.0,
            )
        ],
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        periods=[_period(period_end, provider="yfinance", revenue=500.0)],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "NYSE", "REIT"
    ).fundamentals

    period = merged.periods[0]
    assert period.distribution_per_unit == pytest.approx(0.12)
    assert period.nav_per_unit == pytest.approx(1.60)
    assert (
        period.sources["distribution_per_unit"].concept
        == "derived_distribution_per_unit"
    )
    assert period.sources["distribution_per_unit"].unit == "USD/unit"
    assert period.sources["nav_per_unit"].concept == "derived_nav_per_unit"
    assert period.sources["nav_per_unit"].unit == "USD/unit"
    assert merged.sources["distribution_per_unit"] == (
        "derived_distribution_per_unit"
    )
    assert merged.sources["nav_per_unit"] == "derived_nav_per_unit"
    assert "distribution_per_unit" not in merged.missing_fields
    assert "nav_per_unit" not in merged.missing_fields


def test_sec_reit_finalizes_primary_when_optional_yfinance_fallback_fails(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    primary = _fundamentals(
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[
            _period(
                period_end,
                common_dividends=120.0,
                common_equity=1_600.0,
                diluted_shares=1_000.0,
            )
        ],
        warnings=["SEC primary warning"],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: primary,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: (_ for _ in ()).throw(
            YFinanceStatementsError("fallback unavailable")
        ),
    )

    envelope = valuation_fundamentals.get_fundamentals("NYSE", "REIT")

    period = envelope.fundamentals.periods[0]
    assert period.distribution_per_unit == pytest.approx(0.12)
    assert period.nav_per_unit == pytest.approx(1.60)
    assert envelope.fundamentals.missing_fields == []
    assert "SEC primary warning" in envelope.warnings
    assert any("fallback unavailable" in warning for warning in envelope.warnings)


@pytest.mark.parametrize(
    ("fallback_updates", "warning_text"),
    [
        ({"symbol": "NYSE:OTHER"}, "symbol or exchange"),
        ({"currency": "SGD"}, "fallback currency"),
    ],
)
def test_sec_reit_finalizes_primary_when_yfinance_fallback_is_rejected(
    monkeypatch,
    fallback_updates,
    warning_text,
):
    period_end = date(2025, 12, 31)
    primary = _fundamentals(
        exchange="NYSE",
        symbol="NYSE:REIT",
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[
            _period(
                period_end,
                common_dividends=120.0,
                common_equity=1_600.0,
                diluted_shares=1_000.0,
            )
        ],
        warnings=["SEC primary warning"],
    )
    fallback_values = {
        "exchange": "NYSE",
        "symbol": "NYSE:REIT",
        "primary_source": "yfinance_fallback",
        "provider_security_type": "REIT",
        "sector": "Real Estate",
        "industry": "REIT - Retail",
        "periods": [
            _period(period_end, provider="yfinance", revenue=500.0)
        ],
    }
    fallback_values.update(fallback_updates)
    fallback = _fundamentals(**fallback_values)
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: primary,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: fallback,
    )

    envelope = valuation_fundamentals.get_fundamentals("NYSE", "REIT")

    period = envelope.fundamentals.periods[0]
    assert period.distribution_per_unit == pytest.approx(0.12)
    assert period.nav_per_unit == pytest.approx(1.60)
    assert envelope.fundamentals.missing_fields == []
    assert "SEC primary warning" in envelope.warnings
    assert any(warning_text in warning for warning in envelope.warnings)


@pytest.mark.parametrize(
    ("invalid_field", "invalid_unit"),
    [
        ("common_dividends", "shares"),
        ("common_equity", "shares"),
        ("diluted_shares", "USD"),
    ],
)
def test_sec_facade_does_not_derive_reit_values_from_incompatible_units(
    monkeypatch,
    invalid_field,
    invalid_unit,
):
    period_end = date(2025, 12, 31)
    period = _period(
        period_end,
        common_dividends=120.0,
        common_equity=1_600.0,
        diluted_shares=1_000.0,
    )
    invalid_source = period.sources[invalid_field].model_copy(
        update={"unit": invalid_unit}
    )
    period = period.model_copy(
        update={
            "sources": period.sources
            | {invalid_field: invalid_source}
        }
    )
    sec = _fundamentals(
        provider_security_type="REIT",
        industry="REIT - Retail",
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[period],
    )
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        provider_security_type="REIT",
        industry="REIT - Retail",
        periods=[_period(period_end, provider="yfinance", revenue=500.0)],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    result = valuation_fundamentals.get_fundamentals(
        "NYSE", "REIT"
    ).fundamentals.periods[0]

    if invalid_field in {"common_dividends", "diluted_shares"}:
        assert result.distribution_per_unit is None
    if invalid_field in {"common_equity", "diluted_shares"}:
        assert result.nav_per_unit is None


def test_sec_facade_ignores_incompatible_reit_fallback_units(monkeypatch):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        provider_security_type="REIT",
        industry="REIT - Retail",
        missing_fields=["distribution_per_unit", "nav_per_unit"],
        periods=[_period(period_end, revenue=500.0)],
    )
    invalid = _period(
        period_end,
        provider="yfinance",
        distribution_per_unit=0.13,
        nav_per_unit=1.65,
    )
    invalid_sources = {
        field: source.model_copy(update={"unit": "USD"})
        for field, source in invalid.sources.items()
    }
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        provider_security_type="REIT",
        industry="REIT - Retail",
        periods=[invalid.model_copy(update={"sources": invalid_sources})],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("NYSE", "REIT")

    period = envelope.fundamentals.periods[0]
    assert period.distribution_per_unit is None
    assert period.nav_per_unit is None
    assert any("unit or period" in warning for warning in envelope.warnings)


def test_facade_never_infers_reit_metrics_from_balance_sheet_rows(monkeypatch):
    period_end = date(2025, 12, 31)
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:M44U",
        currency="SGD",
        primary_source="yfinance_sgx",
        provider_security_type="REIT",
        industry="REIT - Retail",
        periods=[
            _period(
                period_end,
                currency="SGD",
                provider="yfinance",
                total_assets=2_000.0,
                total_debt=700.0,
            )
        ],
        sources={
            "financial_statements": "yfinance",
            "aggregate_leverage": "unrelated_provider_metadata",
        },
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "SGX", "M44U"
    ).fundamentals

    assert merged.reit_metrics == {}
    assert "aggregate_leverage" not in merged.sources


def test_facade_sanitizes_mutated_reit_metrics_and_stale_sources(monkeypatch):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:M44U",
        currency="SGD",
        primary_source="yfinance_sgx",
        provider_security_type="REIT",
        industry="REIT - Retail",
        reit_metrics={"aggregate_leverage": 0.40},
        sources={
            "financial_statements": "yfinance",
            "aggregate_leverage": "yfinance_info.aggregateLeverage",
            "occupancy": "stale_occupancy_source",
            "wale_years": "stale_wale_source",
            "interest_coverage": "stale_interest_source",
            "ffo": "unapproved_metric_source",
        },
    )
    yahoo.reit_metrics.update(
        {
            "occupancy": float("nan"),
            "wale_years": True,
            "ffo": 100.0,
        }
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    merged = valuation_fundamentals.get_fundamentals(
        "SGX", "M44U"
    ).fundamentals

    assert merged.reit_metrics == {"aggregate_leverage": 0.40}
    assert merged.sources["aggregate_leverage"] == (
        "yfinance_info.aggregateLeverage"
    )
    for invalid_source in (
        "occupancy",
        "wale_years",
        "interest_coverage",
        "ffo",
    ):
        assert invalid_source not in merged.sources


@pytest.mark.parametrize(
    ("fallback_currency", "fallback_period_end"),
    [
        ("SGD", date(2025, 12, 31)),
        ("USD", date(2025, 9, 30)),
    ],
)
def test_sec_fallback_does_not_mix_currency_or_period(
    monkeypatch,
    fallback_currency,
    fallback_period_end,
):
    sec_period_end = date(2025, 12, 31)
    sec = _fundamentals(
        missing_fields=["capital_expenditure"],
        periods=[
            _period(
                sec_period_end,
                operating_cash_flow=100.0,
                revenue=300.0,
                capital_expenditure=None,
            )
        ],
    )
    yahoo = _fundamentals(
        currency=fallback_currency,
        primary_source="yfinance_fallback",
        periods=[
            _period(
                fallback_period_end,
                currency=fallback_currency,
                provider="yfinance",
                capital_expenditure=-20.0,
            )
        ],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("NASDAQ", "ACME")

    assert envelope.fundamentals.periods[0].capital_expenditure is None
    assert "capital_expenditure" in envelope.fundamentals.missing_fields


def test_sec_fallback_rejects_incompatible_fact_unit(monkeypatch):
    period_end = date(2025, 12, 31)
    sec = _fundamentals(
        missing_fields=["capital_expenditure"],
        periods=[_period(period_end, capital_expenditure=None)],
    )
    invalid_source = FactProvenance(
        provider="yfinance",
        concept="Capital Expenditure",
        period_end=period_end,
        unit="shares",
    )
    yahoo_period = _period(
        period_end,
        provider="yfinance",
        capital_expenditure=-20.0,
    ).model_copy(update={"sources": {"capital_expenditure": invalid_source}})
    yahoo = _fundamentals(
        primary_source="yfinance_fallback",
        periods=[yahoo_period],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: sec,
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("NASDAQ", "ACME")

    assert envelope.fundamentals.periods[0].capital_expenditure is None
    assert any("unit" in warning.lower() for warning in envelope.warnings)


@pytest.mark.parametrize("sec_user_agent", [None, "   "])
def test_missing_sec_configuration_uses_yfinance_with_warning(
    monkeypatch, sec_user_agent
):
    yahoo = _fundamentals(primary_source="yfinance_fallback")
    monkeypatch.setattr(
        valuation_fundamentals,
        "get_settings",
        lambda: _settings(sec_user_agent=sec_user_agent),
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: pytest.fail("SEC must not be called"),
    )
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("NASDAQ", "ACME")

    assert envelope.fundamentals.primary_source == "yfinance_fallback"
    assert any("SEC_USER_AGENT" in warning for warning in envelope.warnings)
    assert envelope.fundamentals.warnings == list(envelope.warnings)


def test_sgx_goes_directly_to_yfinance_and_records_medium_confidence_cap(
    monkeypatch,
):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    calls = 0

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        nonlocal calls
        calls += 1
        return yahoo

    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: pytest.fail("SEC must not be called for SGX"),
    )
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    first = valuation_fundamentals.get_fundamentals("sgx", "d05")
    second = valuation_fundamentals.get_fundamentals("SGX", "D05.SI")

    assert calls == 1
    assert first == second
    assert first.fundamentals.primary_source == "yfinance_sgx"
    assert any("medium" in warning.lower() for warning in first.warnings)


def test_complete_sgx_reit_drops_ordinary_gaps_and_keeps_medium_confidence(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:M44U",
        currency="SGD",
        primary_source="yfinance_sgx",
        provider_security_type="REIT",
        sector="Real Estate",
        industry="REIT - Retail",
        current_diluted_shares=1_000.0,
        periods=[
            _period(
                period_end,
                currency="SGD",
                provider="yfinance",
                diluted_shares=1_000.0,
                distribution_per_unit=0.08,
                nav_per_unit=1.20,
            )
        ],
        missing_fields=[
            "operating_cash_flow",
            "capital_expenditure",
            "net_income_common",
        ],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("SGX", "M44U")
    model_result = ModelResult(
        method="reit_distribution_nav",
        detected_company_type="reit",
        bear=0.80,
        base=1.00,
        bull=1.20,
        details={"usable_years": 3},
        assumptions={},
        quality={},
    )

    assert envelope.fundamentals.missing_fields == []
    assert (
        valuation_service._confidence(
            envelope.fundamentals,
            envelope,
            model_result,
        )
        == "medium"
    )


def test_expired_refresh_failure_returns_usable_stale_entry(monkeypatch):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    now = [0.0]
    calls = 0

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise YFinanceStatementsError("temporary Yahoo failure")
        return yahoo

    monkeypatch.setattr(valuation_fundamentals, "monotonic", lambda: now[0])
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    fresh = valuation_fundamentals.get_fundamentals("SGX", "D05")
    now[0] = 61.0
    stale = valuation_fundamentals.get_fundamentals("SGX", "D05")

    assert fresh.stale is False
    assert stale.stale is True
    assert stale.fundamentals == fresh.fundamentals
    assert stale.fresh_until == fresh.fresh_until
    assert stale.stale_until == fresh.stale_until
    assert any("stale" in warning.lower() for warning in stale.warnings)


def test_slow_refresh_failure_cannot_serve_entry_past_stale_deadline(monkeypatch):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    now = [0.0]
    calls = 0

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        nonlocal calls
        calls += 1
        if calls > 1:
            now[0] = 301.0
            raise YFinanceStatementsError("slow Yahoo failure")
        return yahoo

    monkeypatch.setattr(valuation_fundamentals, "monotonic", lambda: now[0])
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    valuation_fundamentals.get_fundamentals("SGX", "D05")
    now[0] = 61.0

    with pytest.raises(YFinanceStatementsError, match="slow Yahoo failure"):
        valuation_fundamentals.get_fundamentals("SGX", "D05")


def test_expired_refresh_is_single_flight_and_waiters_use_installed_result(
    monkeypatch,
):
    initial = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
        industry="Initial",
    )
    slow = initial.model_copy(update={"industry": "Slow owner result"})
    fast = initial.model_copy(update={"industry": "Fast duplicate result"})
    now = [0.0]
    calls = 0
    calls_lock = Lock()
    slow_started = Event()
    duplicate_started = Event()
    release_slow = Event()
    waiter_waiting = Event()
    results: list[ValuationFundamentals] = []
    errors: list[BaseException] = []

    class TrackingRefreshEvent:
        def __init__(self) -> None:
            self._inner = Event()

        def wait(self, timeout: float | None = None) -> bool:
            waiter_waiting.set()
            return self._inner.wait(timeout)

        def set(self) -> None:
            self._inner.set()

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            return initial
        if call_number == 2:
            slow_started.set()
            assert release_slow.wait(2), "test did not release slow refresh"
            return slow
        duplicate_started.set()
        return fast

    def call_facade() -> None:
        try:
            results.append(
                valuation_fundamentals.get_fundamentals(
                    "SGX", "D05"
                ).fundamentals
            )
        except BaseException as exc:  # noqa: BLE001 - thread assertion relay.
            errors.append(exc)

    monkeypatch.setattr(valuation_fundamentals, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        valuation_fundamentals, "Event", lambda: TrackingRefreshEvent()
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    valuation_fundamentals.get_fundamentals("SGX", "D05")
    now[0] = 61.0
    owner = Thread(target=call_facade)
    waiter = Thread(target=call_facade)
    owner.start()
    assert slow_started.wait(2), "owner refresh did not start"
    waiter.start()
    assert waiter_waiting.wait(2), "waiter did not join in-flight refresh"
    release_slow.set()
    owner.join(2)
    waiter.join(2)

    assert not owner.is_alive()
    assert not waiter.is_alive()
    assert errors == []
    assert duplicate_started.is_set() is False
    assert calls == 2
    assert [result.industry for result in results] == [
        "Slow owner result",
        "Slow owner result",
    ]
    assert (
        valuation_fundamentals.get_fundamentals(
            "SGX", "D05"
        ).fundamentals.industry
        == "Slow owner result"
    )
    assert calls == 2


def test_refresh_owner_notifies_waiters_when_cache_entry_build_fails(monkeypatch):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    provider_started = Event()
    release_provider = Event()
    waiter_waiting = Event()
    inner_flight_event = Event()
    settings_calls = 0
    provider_calls = 0
    errors: list[BaseException] = []

    class TrackingFlightEvent:
        def wait(self, timeout: float | None = None) -> bool:
            waiter_waiting.set()
            return inner_flight_event.wait(timeout)

        def set(self) -> None:
            inner_flight_event.set()

    def settings() -> SimpleNamespace:
        nonlocal settings_calls
        settings_calls += 1
        if settings_calls == 2:
            raise RuntimeError("cache entry build failed")
        return _settings()

    def fetch_yahoo(exchange: str, symbol: str) -> ValuationFundamentals:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            provider_started.set()
            assert release_provider.wait(2), "test did not release provider"
        return yahoo

    def call_facade() -> None:
        try:
            valuation_fundamentals.get_fundamentals("SGX", "D05")
        except BaseException as exc:  # noqa: BLE001 - thread assertion relay.
            errors.append(exc)

    monkeypatch.setattr(
        valuation_fundamentals, "Event", lambda: TrackingFlightEvent()
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", settings)
    monkeypatch.setattr(
        valuation_fundamentals, "fetch_yfinance_fundamentals", fetch_yahoo
    )

    owner = Thread(target=call_facade)
    waiter = Thread(target=call_facade)
    owner.start()
    assert provider_started.wait(2), "owner provider call did not start"
    waiter.start()
    assert waiter_waiting.wait(2), "waiter did not observe in-flight refresh"
    release_provider.set()
    owner.join(2)
    waiter.join(0.2)
    waiter_was_stuck = waiter.is_alive()
    if waiter_was_stuck:
        valuation_fundamentals._clear_cache()
        waiter.join(2)

    assert not owner.is_alive()
    assert not waiter.is_alive()
    assert waiter_was_stuck is False
    assert [str(error) for error in errors] == [
        "cache entry build failed",
        "cache entry build failed",
    ]


def test_provider_failure_without_usable_stale_entry_remains_typed(monkeypatch):
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: (_ for _ in ()).throw(
            YFinanceStatementsError("Yahoo unavailable")
        ),
    )

    with pytest.raises(YFinanceStatementsError, match="Yahoo unavailable"):
        valuation_fundamentals.get_fundamentals("SGX", "D05")


def test_sec_provider_failure_without_stale_entry_remains_typed(monkeypatch):
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_sec_fundamentals",
        lambda exchange, symbol: (_ for _ in ()).throw(
            SecCompanyFactsError("SEC unavailable", retry_after_s=60)
        ),
    )

    with pytest.raises(SecCompanyFactsError, match="SEC unavailable") as exc_info:
        valuation_fundamentals.get_fundamentals("NASDAQ", "ACME")

    assert exc_info.value.retry_after_s == 60


def test_fundamentals_envelope_is_immutable(monkeypatch):
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    envelope = valuation_fundamentals.get_fundamentals("SGX", "D05")

    with pytest.raises(FrozenInstanceError):
        envelope.stale = True


def test_cached_fundamentals_are_deeply_isolated_from_callers_and_provider(
    monkeypatch,
):
    period_end = date(2025, 12, 31)
    yahoo = _fundamentals(
        exchange="SGX",
        symbol="SGX:D05",
        currency="SGD",
        primary_source="yfinance_sgx",
        periods=[
            _period(
                period_end,
                currency="SGD",
                provider="yfinance",
                operating_cash_flow=100.0,
                capital_expenditure=-20.0,
                revenue=300.0,
            )
        ],
        warnings=["provider warning"],
    )
    monkeypatch.setattr(valuation_fundamentals, "get_settings", _settings)
    monkeypatch.setattr(
        valuation_fundamentals,
        "fetch_yfinance_fundamentals",
        lambda exchange, symbol: yahoo,
    )

    first = valuation_fundamentals.get_fundamentals("SGX", "D05")
    first.fundamentals.industry = "caller mutation"
    first.fundamentals.warnings.append("caller warning")
    first.fundamentals.sources["financial_statements"] = "caller source"
    first.fundamentals.periods[0].sources[
        "operating_cash_flow"
    ] = FactProvenance(
        provider="caller",
        concept="mutated",
        period_end=period_end,
        unit="SGD",
    )
    first.fundamentals.periods.append(
        _period(
            date(2024, 12, 31),
            currency="SGD",
            provider="caller",
            revenue=1.0,
        )
    )
    yahoo.periods[0].sources["capital_expenditure"] = FactProvenance(
        provider="provider mutation",
        concept="mutated",
        period_end=period_end,
        unit="SGD",
    )

    second = valuation_fundamentals.get_fundamentals("SGX", "D05")

    assert second is not first
    assert second.fundamentals is not first.fundamentals
    assert second.fundamentals.industry == "Software"
    assert second.fundamentals.warnings == [
        "provider warning",
        "SGX yFinance fundamentals cap valuation confidence at medium.",
    ]
    assert second.fundamentals.sources == {
        "financial_statements": "yfinance_sgx"
    }
    assert len(second.fundamentals.periods) == 1
    assert (
        second.fundamentals.periods[0]
        .sources["operating_cash_flow"]
        .provider
        == "yfinance"
    )
    assert (
        second.fundamentals.periods[0]
        .sources["capital_expenditure"]
        .provider
        == "yfinance"
    )
