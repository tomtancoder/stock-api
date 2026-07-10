import math
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from app.services.reit_valuation import (
    ReitNormalizedInputs,
    normalize_reit_history,
    value_reit,
)
from app.services.valuation_router import ValuationUnreliable
from app.services.valuation_types import FinancialPeriod, ModelResult, ValuationFundamentals


def _period(
    year: int,
    *,
    dpu: float | None = None,
    nav_per_unit: float | None = None,
    is_ttm: bool = False,
    period_end: date | None = None,
    currency: str = "SGD",
) -> FinancialPeriod:
    return FinancialPeriod(
        period_end=period_end or date(year, 12, 31),
        fiscal_year=year,
        is_ttm=is_ttm,
        currency=currency,
        distribution_per_unit=dpu,
        nav_per_unit=nav_per_unit,
    )


def _fundamentals(
    periods: list[FinancialPeriod], **overrides: object
) -> ValuationFundamentals:
    values: dict[str, object] = {
        "symbol": "SGX:REIT",
        "exchange": "SGX",
        "currency": "SGD",
        "primary_source": "test",
        "provider_security_type": "REIT",
        "current_diluted_shares": 1_000_000.0,
        "periods": periods,
        "fetched_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ValuationFundamentals(**values)


def _reit_periods(*, include_nav: bool = True) -> list[FinancialPeriod]:
    navs = [0.98, 1.02, 1.06, 1.08, 1.10] if include_nav else [None] * 5
    return [
        _period(year, dpu=dpu, nav_per_unit=nav)
        for year, dpu, nav in zip(
            range(2021, 2026), [0.04, 0.05, 0.055, 0.06, 0.065], navs
        )
    ]


def test_normalize_reit_history_uses_median_of_independent_dpu_components():
    periods = _reit_periods()
    periods.append(
        _period(
            2026,
            dpu=0.07,
            is_ttm=True,
            period_end=date(2026, 6, 30),
        )
    )

    normalized = normalize_reit_history(_fundamentals(periods))

    weighted_three_year_dpu = (0.055 + 2 * 0.06 + 3 * 0.065) / 6
    five_year_median_dpu = 0.055
    assert normalized.normalized_dpu == pytest.approx(
        sorted([0.07, weighted_three_year_dpu, five_year_median_dpu])[1]
    )
    assert normalized.current_nav_per_unit == pytest.approx(1.10)
    assert normalized.usable_years == 5


def test_normalize_reit_history_uses_three_year_and_history_median_without_ttm():
    normalized = normalize_reit_history(
        _fundamentals(
            [
                _period(2023, dpu=0.04, nav_per_unit=1.00),
                _period(2024, dpu=0.06, nav_per_unit=1.05),
                _period(2025, dpu=0.08, nav_per_unit=1.10),
            ]
        )
    )

    weighted_three_year_dpu = (0.04 + 2 * 0.06 + 3 * 0.08) / 6
    assert normalized.normalized_dpu == pytest.approx(
        (0.06 + weighted_three_year_dpu) / 2
    )
    assert normalized.usable_years == 3


def test_normalize_reit_history_clamps_positive_and_negative_dpu_and_nav_growth():
    growing = normalize_reit_history(
        _fundamentals(
            [
                _period(2023, dpu=0.04, nav_per_unit=1.00),
                _period(2024, dpu=0.05, nav_per_unit=1.10),
                _period(2025, dpu=0.06, nav_per_unit=1.20),
            ]
        )
    )
    declining = normalize_reit_history(
        _fundamentals(
            [
                _period(2023, dpu=0.08, nav_per_unit=1.20),
                _period(2024, dpu=0.06, nav_per_unit=1.00),
                _period(2025, dpu=0.04, nav_per_unit=0.80),
            ]
        )
    )

    assert growing.base_dpu_growth == 0.03
    assert growing.base_nav_growth == 0.025
    assert declining.base_dpu_growth == -0.03
    assert declining.base_nav_growth == -0.02


def test_normalized_reit_inputs_are_immutable_and_reject_invalid_values():
    normalized = ReitNormalizedInputs(
        normalized_dpu=0.06,
        current_nav_per_unit=1.10,
        base_dpu_growth=0.01,
        base_nav_growth=0.01,
        usable_years=3,
    )

    with pytest.raises(FrozenInstanceError):
        normalized.normalized_dpu = 0.07
    with pytest.raises(ValueError, match="normalized DPU"):
        ReitNormalizedInputs(0.0, 1.10, 0.01, 0.01, 3)
    with pytest.raises(ValueError, match="DPU growth"):
        ReitNormalizedInputs(0.06, 1.10, 0.04, 0.01, 3)
    with pytest.raises(ValueError, match="usable years"):
        ReitNormalizedInputs(0.06, 1.10, 0.01, 0.01, 2)


def test_value_reit_projects_exact_distribution_and_terminal_nav_contributions():
    result = value_reit(_fundamentals(_reit_periods(), reit_metrics={"occupancy": 0.96}))

    assert isinstance(result, ModelResult)
    assert result.method == "reit_distribution_nav"
    assert result.detected_company_type == "reit"
    scenarios = result.assumptions["scenarios"]
    base_dpu_growth = result.details["base_dpu_growth"]
    base_nav_growth = result.details["base_nav_growth"]
    assert scenarios["bear"] == {
        "starting_dpu_factor": 0.90,
        "dpu_growth": max(-0.05, base_dpu_growth - 0.02),
        "required_return": 0.10,
        "nav_growth": max(-0.02, base_nav_growth - 0.01),
        "terminal_nav_factor": 0.85,
    }
    assert scenarios["base"] == {
        "starting_dpu_factor": 1.00,
        "dpu_growth": base_dpu_growth,
        "required_return": 0.085,
        "nav_growth": base_nav_growth,
        "terminal_nav_factor": 1.00,
    }
    assert scenarios["bull"] == {
        "starting_dpu_factor": 1.05,
        "dpu_growth": min(0.05, base_dpu_growth + 0.02),
        "required_return": 0.07,
        "nav_growth": min(0.025, base_nav_growth + 0.01),
        "terminal_nav_factor": 1.10,
    }
    for name in ("bear", "base", "bull"):
        contribution = result.details["scenario_contributions"][name]
        assert contribution["pv_distributions"] > 0
        assert contribution["pv_terminal_nav"] > 0
        assert result.details["present_value_distributions"][name] == pytest.approx(
            contribution["pv_distributions"]
        )
        assert result.details["present_value_terminal"][name] == pytest.approx(
            contribution["pv_terminal_nav"]
        )
        assert contribution["pv_distributions"] + contribution["pv_terminal_nav"] == pytest.approx(
            getattr(result, name), abs=0.0001
        )
    assert result.bear <= result.base <= result.bull
    assert result.details["normalized_dpu"] == pytest.approx(
        (0.055 + (0.055 + 2 * 0.06 + 3 * 0.065) / 6) / 2
    )
    assert result.details["nav_per_unit"] == pytest.approx(1.10)
    assert result.details["price_to_nav"] is None
    assert result.details["distribution_yield"] is None
    assert result.details["occupancy"] == pytest.approx(0.96)


def test_value_reit_uses_distribution_only_gordon_fallback_when_nav_is_missing():
    result = value_reit(_fundamentals(_reit_periods(include_nav=False)))

    assert result.method == "reit_distribution_only"
    assert result.details["nav_per_unit"] is None
    assert result.quality["confidence"] == "low"
    assert result.quality["details"]["distribution_only"] is True
    assert any("NAV" in warning for warning in result.warnings)
    for name, terminal_growth in (("bear", 0.0), ("base", 0.015), ("bull", 0.025)):
        assumptions = result.assumptions["scenarios"][name]
        assert assumptions["terminal_growth"] == terminal_growth
        assert assumptions["required_return"] > terminal_growth
        contribution = result.details["scenario_contributions"][name]
        assert contribution["pv_distributions"] > 0
        assert contribution["pv_terminal_value"] > 0
        assert result.details["present_value_distributions"][name] == pytest.approx(
            contribution["pv_distributions"]
        )
        assert result.details["present_value_terminal"][name] == pytest.approx(
            contribution["pv_terminal_value"]
        )
        assert contribution["pv_distributions"] + contribution["pv_terminal_value"] == pytest.approx(
            getattr(result, name), abs=0.0001
        )
    assert result.bear <= result.base <= result.bull


def test_value_reit_rejects_invalid_supplied_nav_instead_of_using_fallback():
    periods = [
        _period(year, dpu=dpu, nav_per_unit=-1.0)
        for year, dpu in zip(range(2023, 2026), [0.05, 0.06, 0.07])
    ]

    with pytest.raises(ValuationUnreliable, match="invalid NAV"):
        value_reit(_fundamentals(periods))


def test_value_reit_translates_non_finite_projection_output_to_unreliable():
    periods = [
        _period(year, dpu=2.5e307, nav_per_unit=2.5e307)
        for year in range(2023, 2026)
    ]

    with pytest.raises(ValuationUnreliable, match="non-finite"):
        value_reit(_fundamentals(periods))


def test_value_reit_reports_optional_reit_metric_gaps_without_blocking():
    result = value_reit(_fundamentals(_reit_periods()))

    assert result.quality["confidence"] == "medium"
    assert result.details["occupancy"] is None
    assert "occupancy" in result.quality["details"]["missing_reit_metrics"]
    assert any("Missing optional REIT metrics" in warning for warning in result.warnings)


def test_value_reit_requires_positive_current_units():
    fundamentals = _fundamentals(_reit_periods(), current_diluted_shares=None)

    with pytest.raises(ValueError, match="current units"):
        normalize_reit_history(fundamentals)
    with pytest.raises(ValuationUnreliable) as exc_info:
        value_reit(fundamentals)

    assert exc_info.value.reasons == ["REIT valuation requires positive current units"]


@pytest.mark.parametrize(
    "periods",
    [
        [_period(2024, dpu=0.05), _period(2025, dpu=0.06)],
        [_period(2023, dpu=0.05), _period(2024, dpu=0.0), _period(2025, dpu=0.06)],
    ],
)
def test_normalize_reit_history_rejects_insufficient_usable_dpu_years(periods):
    with pytest.raises(ValueError, match="three usable"):
        normalize_reit_history(_fundamentals(periods))


def test_value_reit_raises_existing_unreliable_error_for_insufficient_dpu_history():
    with pytest.raises(ValuationUnreliable) as exc_info:
        value_reit(_fundamentals([_period(2024, dpu=0.05), _period(2025, dpu=0.06)]))

    assert any("three usable" in reason for reason in exc_info.value.reasons)


@pytest.mark.parametrize("field", ["dpu", "nav_per_unit"])
def test_normalize_reit_history_rejects_non_finite_observations(field):
    periods = _reit_periods()
    periods[-1] = _period(
        2025,
        **{field: float("nan")},
        **({"nav_per_unit": 1.10} if field == "dpu" else {"dpu": 0.065}),
    )

    with pytest.raises(ValueError, match="finite"):
        normalize_reit_history(_fundamentals(periods))
