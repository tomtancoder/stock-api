import math
from datetime import date, datetime, timezone

import pytest

from app.services import owner_earnings_valuation, valuation_math
from app.services.owner_earnings_valuation import (
    calculate_period_owner_earnings,
    normalize_owner_earnings,
    value_owner_earnings,
)
from app.services.valuation_types import FinancialPeriod, ValuationFundamentals


@pytest.fixture
def period_factory():
    def factory(**overrides) -> FinancialPeriod:
        values = {
            "period_end": date(2025, 12, 31),
            "currency": "USD",
            "operating_cash_flow": 100.0,
            "capital_expenditure": -20.0,
            "stock_based_compensation": 0.0,
            "interest_paid_outside_operating": 0.0,
        }
        values.update(overrides)
        return FinancialPeriod(**values)

    return factory


@pytest.fixture
def fundamentals_factory():
    def factory(periods, **overrides) -> ValuationFundamentals:
        values = {
            "symbol": "NASDAQ:ACME",
            "exchange": "NASDAQ",
            "currency": "USD",
            "primary_source": "test",
            "current_diluted_shares": 100.0,
            "periods": periods,
            "fetched_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return ValuationFundamentals(**values)

    return factory


def owner_earnings_period(period_factory, year, owner_earnings, **overrides):
    values = {
        "period_end": date(year, 12, 31),
        "operating_cash_flow": owner_earnings,
        "capital_expenditure": 0.0,
        "stock_based_compensation": 0.0,
        "interest_paid_outside_operating": 0.0,
        "diluted_shares": 100.0,
    }
    values.update(overrides)
    return period_factory(**values)


@pytest.fixture
def stable_fundamentals(period_factory, fundamentals_factory):
    annual = [
        owner_earnings_period(
            period_factory,
            year,
            1_000.0,
            revenue=10_000.0 * (1.08**index),
        )
        for index, year in enumerate(range(2021, 2026))
    ]
    ttm = owner_earnings_period(
        period_factory,
        2026,
        1_000.0,
        is_ttm=True,
        revenue=11_664.0,
    )
    return fundamentals_factory([ttm, *reversed(annual)])


@pytest.mark.parametrize("currency", ["USD", "SGD"])
def test_owner_earnings_subtracts_sbc_and_external_interest_once(
    period_factory, currency
):
    period = period_factory(
        currency=currency,
        operating_cash_flow=120,
        capital_expenditure=-30,
        stock_based_compensation=10,
        interest_paid_outside_operating=5,
    )

    assert calculate_period_owner_earnings(period) == 75.0


def test_owner_earnings_does_not_subtract_working_capital_again(period_factory):
    period = period_factory(
        operating_cash_flow=100,
        capital_expenditure=20,
        stock_based_compensation=0,
        interest_paid_outside_operating=0,
    )

    assert calculate_period_owner_earnings(period) == 80.0


@pytest.mark.parametrize("field", ["operating_cash_flow", "capital_expenditure"])
def test_owner_earnings_requires_cash_flow_and_capex(period_factory, field):
    period = period_factory(**{field: None})

    assert calculate_period_owner_earnings(period) is None


@pytest.mark.parametrize(
    "field", ["stock_based_compensation", "interest_paid_outside_operating"]
)
def test_owner_earnings_requires_resolved_adjustment_facts(period_factory, field):
    period = period_factory(**{field: None})

    assert calculate_period_owner_earnings(period) is None


def test_normalized_owner_earnings_uses_median_of_available_components(
    period_factory, fundamentals_factory
):
    annual = [
        owner_earnings_period(
            period_factory,
            year,
            owner_earnings,
            revenue=owner_earnings * 10,
        )
        for year, owner_earnings in zip(
            range(2021, 2026), [600.0, 700.0, 800.0, 900.0, 1_000.0]
        )
    ]
    ttm = owner_earnings_period(
        period_factory,
        2026,
        1_100.0,
        is_ttm=True,
        revenue=12_000.0,
    )
    fundamentals = fundamentals_factory([ttm, *reversed(annual)])

    assert normalize_owner_earnings(fundamentals) == pytest.approx(1_100.0)


def test_normalized_owner_earnings_requires_two_independent_components(
    period_factory, fundamentals_factory
):
    annual = [
        owner_earnings_period(period_factory, year, owner_earnings)
        for year, owner_earnings in zip(
            range(2023, 2026), [800.0, 900.0, 1_000.0]
        )
    ]

    with pytest.raises(ValueError, match="two independent"):
        normalize_owner_earnings(fundamentals_factory(annual))


def test_normalized_owner_earnings_requires_three_positive_annual_periods(
    period_factory, fundamentals_factory
):
    annual = [
        owner_earnings_period(period_factory, 2023, 800.0),
        owner_earnings_period(period_factory, 2024, 900.0),
        owner_earnings_period(period_factory, 2025, -100.0),
    ]
    ttm = owner_earnings_period(period_factory, 2026, 1_000.0, is_ttm=True)

    with pytest.raises(ValueError, match="three positive"):
        normalize_owner_earnings(fundamentals_factory([*annual, ttm]))


def test_normalized_owner_earnings_rejects_incompatible_currencies(
    period_factory, fundamentals_factory
):
    annual = [
        owner_earnings_period(period_factory, 2023, 800.0),
        owner_earnings_period(period_factory, 2024, 900.0, currency="SGD"),
        owner_earnings_period(period_factory, 2025, 1_000.0),
    ]
    ttm = owner_earnings_period(period_factory, 2026, 1_000.0, is_ttm=True)

    with pytest.raises(ValueError, match="currency"):
        normalize_owner_earnings(fundamentals_factory([*annual, ttm]))


def test_owner_earnings_model_returns_finite_ordered_scenarios(stable_fundamentals):
    result = value_owner_earnings(stable_fundamentals)

    assert result.method == "owner_earnings_dcf"
    assert result.detected_company_type == "operating_company"
    assert result.details["normalized_owner_earnings"] == pytest.approx(1_000.0)
    assert result.details["owner_earnings_per_share"] == pytest.approx(10.0)
    assert result.details["derived_growth"] == pytest.approx(0.04)
    assert all(
        math.isfinite(value) and value > 0
        for value in (result.bear, result.base, result.bull)
    )
    assert result.bear <= result.base <= result.bull


def test_owner_earnings_model_reports_all_approved_assumptions(stable_fundamentals):
    result = value_owner_earnings(stable_fundamentals)

    assert result.assumptions == {
        "projection_years": 10,
        "margin_of_safety": 0.25,
        "scenarios": {
            "bear": {
                "starting_factor": 0.90,
                "initial_growth": pytest.approx(0.0),
                "required_return": 0.12,
                "terminal_growth": 0.02,
                "projection_years": 10,
            },
            "base": {
                "starting_factor": 1.00,
                "initial_growth": pytest.approx(0.04),
                "required_return": 0.10,
                "terminal_growth": 0.025,
                "projection_years": 10,
            },
            "bull": {
                "starting_factor": 1.05,
                "initial_growth": pytest.approx(0.07),
                "required_return": 0.08,
                "terminal_growth": 0.03,
                "projection_years": 10,
            },
        },
    }


def test_owner_earnings_model_clamps_declining_per_share_growth(
    period_factory, fundamentals_factory
):
    annual = [
        owner_earnings_period(
            period_factory,
            year,
            owner_earnings,
            revenue=owner_earnings * 10,
        )
        for year, owner_earnings in zip(
            range(2021, 2026), [1_000.0, 700.0, 400.0, 200.0, 100.0]
        )
    ]
    ttm = owner_earnings_period(
        period_factory,
        2026,
        100.0,
        is_ttm=True,
        revenue=1_000.0,
    )

    result = value_owner_earnings(fundamentals_factory([*annual, ttm]))

    assert result.details["derived_growth"] == -0.15
    assert result.assumptions["scenarios"]["base"]["initial_growth"] == -0.15
    assert result.bear <= result.base <= result.bull


def test_owner_earnings_model_calls_shared_scenario_validation(
    stable_fundamentals, monkeypatch
):
    validated = []

    def validate(bear, base, bull):
        validated.append((bear, base, bull))
        valuation_math.validate_scenarios(bear, base, bull)

    monkeypatch.setattr(owner_earnings_valuation, "validate_scenarios", validate)

    result = value_owner_earnings(stable_fundamentals)

    assert validated == [(result.bear, result.base, result.bull)]
