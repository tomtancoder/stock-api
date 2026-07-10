import math
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.services import bank_valuation, valuation_math, valuation_service
from app.services.bank_valuation import (
    BankNormalizedInputs,
    normalize_bank_history,
    value_bank,
)
from app.services.valuation_fundamentals import FundamentalsEnvelope
from app.services.valuation_types import (
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)


def _period(
    year: int,
    *,
    common_equity: float | None,
    net_income_common: float | None = None,
    common_dividends: float | None = None,
    diluted_shares: float | None = 1_000.0,
    currency: str = "USD",
) -> FinancialPeriod:
    return FinancialPeriod(
        period_end=date(year, 12, 31),
        fiscal_year=year,
        currency=currency,
        common_equity=common_equity,
        net_income_common=net_income_common,
        common_dividends=common_dividends,
        diluted_shares=diluted_shares,
    )


def _fundamentals(
    periods: list[FinancialPeriod], **overrides: object
) -> ValuationFundamentals:
    values: dict[str, object] = {
        "symbol": "NYSE:BANK",
        "exchange": "NYSE",
        "currency": "USD",
        "primary_source": "test",
        "current_diluted_shares": 1_000.0,
        "periods": periods,
        "fetched_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ValuationFundamentals(**values)


def _bank_periods(
    equities: list[float],
    *,
    roes: list[float] | None = None,
    payouts: list[float] | None = None,
) -> list[FinancialPeriod]:
    observation_count = len(equities) - 1
    roes = roes or [0.12] * observation_count
    payouts = payouts or [0.40] * observation_count
    assert len(roes) == observation_count
    assert len(payouts) == observation_count

    periods = [_period(2020, common_equity=equities[0])]
    for index, (beginning_equity, ending_equity, roe, payout) in enumerate(
        zip(equities, equities[1:], roes, payouts), start=1
    ):
        net_income = ((beginning_equity + ending_equity) / 2.0) * roe
        periods.append(
            _period(
                2020 + index,
                common_equity=ending_equity,
                net_income_common=net_income,
                common_dividends=-(net_income * payout),
            )
        )
    return periods


def test_normalize_bank_history_uses_average_equity_roe_and_payout():
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])

    normalized = normalize_bank_history(_fundamentals(list(reversed(periods))))

    assert normalized.common_equity == 10_000.0
    assert normalized.diluted_shares == 1_000.0
    assert normalized.book_value_per_share == 10.0
    assert normalized.normalized_roe == pytest.approx(0.12)
    assert normalized.payout_ratio == pytest.approx(0.40)
    assert normalized.usable_years == 4


def test_normalize_bank_history_uses_median_of_last_five_valid_observations():
    periods = _bank_periods(
        [7_000.0, 7_500.0, 8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0],
        roes=[0.80, 0.08, 0.10, 0.12, 0.14, 0.16],
        payouts=[0.90, 0.20, 0.30, 0.40, 0.50, 0.60],
    )

    normalized = normalize_bank_history(_fundamentals(periods))

    assert normalized.normalized_roe == pytest.approx(0.12)
    assert normalized.payout_ratio == pytest.approx(0.40)
    assert normalized.usable_years == 6


def test_normalize_bank_history_requires_three_valid_observations():
    periods = _bank_periods([8_000.0, 9_000.0, 10_000.0])

    with pytest.raises(ValueError, match="three valid"):
        normalize_bank_history(_fundamentals(periods))


@pytest.mark.parametrize("latest_equity", [0.0, -1.0])
def test_normalize_bank_history_requires_positive_current_equity(latest_equity):
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    periods[-1] = periods[-1].model_copy(
        update={"common_equity": latest_equity}
    )

    with pytest.raises(ValueError, match="positive common equity"):
        normalize_bank_history(_fundamentals(periods))


@pytest.mark.parametrize("current_shares", [0.0, -1.0])
def test_normalize_bank_history_requires_positive_current_shares(current_shares):
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])

    with pytest.raises(ValueError, match="positive diluted shares"):
        normalize_bank_history(
            _fundamentals(periods, current_diluted_shares=current_shares)
        )


def test_normalize_bank_history_rejects_payout_above_one():
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    periods[-1] = periods[-1].model_copy(
        update={"common_dividends": -periods[-1].net_income_common * 1.01}
    )

    with pytest.raises(ValueError, match="payout ratio"):
        normalize_bank_history(_fundamentals(periods))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("common_equity", float("inf")),
        ("net_income_common", float("nan")),
        ("common_dividends", float("inf")),
        ("diluted_shares", float("nan")),
    ],
)
def test_normalize_bank_history_rejects_non_finite_history(field, value):
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    periods[-1] = periods[-1].model_copy(update={field: value})

    with pytest.raises(ValueError, match="finite"):
        normalize_bank_history(_fundamentals(periods))


def test_normalize_bank_history_rejects_incompatible_currency():
    periods = _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    periods[2] = periods[2].model_copy(update={"currency": "SGD"})

    with pytest.raises(ValueError, match="currency"):
        normalize_bank_history(_fundamentals(periods))


def test_bank_normalized_inputs_are_immutable_and_validate_payout_bounds():
    normalized = BankNormalizedInputs(
        common_equity=10_000.0,
        diluted_shares=1_000.0,
        normalized_roe=0.12,
        payout_ratio=0.40,
        book_value_per_share=10.0,
        usable_years=5,
    )

    with pytest.raises(FrozenInstanceError):
        normalized.payout_ratio = 0.50
    for invalid_payout in (-0.01, 1.01):
        with pytest.raises(ValueError, match="payout ratio"):
            BankNormalizedInputs(
                common_equity=10_000.0,
                diluted_shares=1_000.0,
                normalized_roe=0.12,
                payout_ratio=invalid_payout,
                book_value_per_share=10.0,
                usable_years=5,
            )


@pytest.mark.parametrize(
    "field",
    [
        "common_equity",
        "diluted_shares",
        "normalized_roe",
        "payout_ratio",
        "book_value_per_share",
    ],
)
def test_bank_normalized_inputs_reject_non_finite_numeric_fields(field):
    values = {
        "common_equity": 10_000.0,
        "diluted_shares": 1_000.0,
        "normalized_roe": 0.12,
        "payout_ratio": 0.40,
        "book_value_per_share": 10.0,
        "usable_years": 5,
    }
    values[field] = float("nan")

    with pytest.raises(ValueError, match="finite"):
        BankNormalizedInputs(**values)


@pytest.mark.parametrize("usable_years", [True, 3.0, float("nan")])
def test_bank_normalized_inputs_require_real_integer_usable_years(usable_years):
    with pytest.raises(ValueError, match="usable years"):
        BankNormalizedInputs(
            common_equity=10_000.0,
            diluted_shares=1_000.0,
            normalized_roe=0.12,
            payout_ratio=0.40,
            book_value_per_share=10.0,
            usable_years=usable_years,
        )


def test_valuation_fundamentals_accepts_only_approved_bank_metric_keys():
    metrics = {
        "cet1_ratio": 0.14,
        "npl_ratio": 0.02,
        "loan_loss_coverage": 1.5,
        "regulatory_capital_headroom": 0.03,
    }

    fundamentals = _fundamentals([], bank_metrics=metrics)

    assert fundamentals.bank_metrics == metrics
    with pytest.raises(ValidationError, match="unsupported bank metric"):
        _fundamentals([], bank_metrics={"tier1_ratio": 0.15})


def test_valuation_fundamentals_rejects_non_finite_bank_metrics():
    with pytest.raises(ValidationError, match="finite"):
        _fundamentals([], bank_metrics={"cet1_ratio": float("nan")})


def _expected_scenario(
    normalized: BankNormalizedInputs,
    starting_factor: float,
    required_return: float,
) -> tuple[float, list[float]]:
    beginning_equity = normalized.common_equity
    present_value_excess_returns = 0.0
    projected_book_equity = []
    starting_roe = normalized.normalized_roe * starting_factor
    for year in range(1, 11):
        progress = (year - 1) / 9
        projected_roe = starting_roe + (
            required_return - starting_roe
        ) * progress
        net_income = beginning_equity * projected_roe
        dividends = normalized.payout_ratio * net_income
        excess_return = (
            projected_roe - required_return
        ) * beginning_equity
        present_value_excess_returns += excess_return / (
            (1 + required_return) ** year
        )
        beginning_equity += net_income - dividends
        projected_book_equity.append(beginning_equity)
    intrinsic_value = (
        normalized.common_equity + present_value_excess_returns
    ) / normalized.diluted_shares
    return round(intrinsic_value, 4), projected_book_equity


def test_value_bank_projects_exact_ten_year_residual_income_scenarios():
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    )
    normalized = normalize_bank_history(fundamentals)

    result = value_bank(fundamentals)

    scenarios = {
        "bear": (0.90, 0.12),
        "base": (1.00, 0.10),
        "bull": (1.05, 0.08),
    }
    for name, (starting_factor, required_return) in scenarios.items():
        expected_value, expected_book_equity = _expected_scenario(
            normalized, starting_factor, required_return
        )
        assert getattr(result, name) == pytest.approx(expected_value)
        assert result.details["projected_book_equity"][name] == pytest.approx(
            expected_book_equity
        )


def test_value_bank_returns_ordered_positive_typed_result_and_contract():
    metrics = {
        "cet1_ratio": 0.14,
        "npl_ratio": 0.02,
        "loan_loss_coverage": 1.5,
        "regulatory_capital_headroom": 0.03,
    }
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0]),
        bank_metrics=metrics,
        warnings=["test source warning"],
    )

    result = value_bank(fundamentals)

    assert isinstance(result, ModelResult)
    assert result.method == "bank_residual_income"
    assert result.detected_company_type == "bank"
    assert all(
        math.isfinite(value) and value > 0
        for value in (result.bear, result.base, result.bull)
    )
    assert result.bear <= result.base <= result.bull
    assert result.details["method"] == "bank_residual_income"
    assert result.details["normalized_roe"] == pytest.approx(0.12)
    assert result.details["book_value_per_share"] == 10.0
    assert result.details["payout_ratio"] == pytest.approx(0.40)
    assert result.details["usable_years"] == 4
    for key, value in metrics.items():
        assert result.details[key] == value
    assert result.quality["eligible"] is True
    assert result.quality["reasons"] == []
    assert result.warnings == ["test source warning"]


def test_value_bank_reports_exact_scenario_assumptions_and_zero_terminal_excess():
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    )

    result = value_bank(fundamentals)

    assert result.assumptions == {
        "projection_years": 10,
        "margin_of_safety": 0.25,
        "terminal_excess_return": 0.0,
        "scenarios": {
            "bear": {"starting_roe_factor": 0.90, "required_return": 0.12},
            "base": {"starting_roe_factor": 1.00, "required_return": 0.10},
            "bull": {"starting_roe_factor": 1.05, "required_return": 0.08},
        },
    }


def test_value_bank_reports_missing_optional_metrics_without_blocking():
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    )

    result = value_bank(fundamentals)

    assert result.details["cet1_ratio"] is None
    assert result.details["npl_ratio"] is None
    assert result.details["loan_loss_coverage"] is None
    assert result.details["regulatory_capital_headroom"] is None
    assert result.quality["eligible"] is True
    assert result.quality["confidence"] == "medium"
    assert result.quality["details"]["missing_bank_metrics"] == [
        "cet1_ratio",
        "loan_loss_coverage",
        "npl_ratio",
        "regulatory_capital_headroom",
    ]
    assert result.warnings == [
        "Missing optional bank metrics: cet1_ratio, loan_loss_coverage, "
        "npl_ratio, regulatory_capital_headroom."
    ]


def test_missing_metrics_cap_five_year_official_confidence_at_medium():
    fundamentals = _fundamentals(
        _bank_periods(
            [
                7_500.0,
                8_000.0,
                8_500.0,
                9_000.0,
                9_500.0,
                10_000.0,
            ]
        ),
        primary_source="sec_companyfacts",
    )
    result = value_bank(fundamentals)
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    envelope = FundamentalsEnvelope(
        fundamentals=fundamentals,
        fresh_until=now + timedelta(days=1),
        stale_until=now + timedelta(days=7),
        stale=False,
        warnings=(),
    )

    assert result.details["usable_years"] == 5
    assert result.quality["confidence"] == "medium"
    assert valuation_service._confidence(fundamentals, envelope, result) == (
        "medium"
    )


def test_value_bank_sanitizes_mutated_optional_metrics():
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0]),
        bank_metrics={
            "cet1_ratio": 0.14,
            "npl_ratio": 0.02,
            "loan_loss_coverage": 1.5,
            "regulatory_capital_headroom": 0.03,
        },
    )
    fundamentals.bank_metrics["cet1_ratio"] = float("nan")
    fundamentals.bank_metrics["npl_ratio"] = True
    fundamentals.bank_metrics["loan_loss_coverage"] = "1.5"
    fundamentals.bank_metrics["tier1_ratio"] = 0.15

    result = value_bank(fundamentals)

    assert result.details["cet1_ratio"] is None
    assert result.details["npl_ratio"] is None
    assert result.details["loan_loss_coverage"] is None
    assert result.details["regulatory_capital_headroom"] == 0.03
    assert "tier1_ratio" not in result.details
    assert result.quality["confidence"] == "medium"
    assert result.quality["details"]["available_bank_metrics"] == [
        "regulatory_capital_headroom"
    ]
    assert result.quality["details"]["missing_bank_metrics"] == [
        "cet1_ratio",
        "loan_loss_coverage",
        "npl_ratio",
    ]
    warning_text = " ".join(result.warnings)
    for metric in (
        "cet1_ratio",
        "loan_loss_coverage",
        "npl_ratio",
        "tier1_ratio",
    ):
        assert metric in warning_text


def test_value_bank_calls_shared_scenario_validation(monkeypatch):
    fundamentals = _fundamentals(
        _bank_periods([8_000.0, 8_500.0, 9_000.0, 9_500.0, 10_000.0])
    )
    validated = []

    def validate(bear, base, bull):
        validated.append((bear, base, bull))
        valuation_math.validate_scenarios(bear, base, bull)

    monkeypatch.setattr(bank_valuation, "validate_scenarios", validate)

    result = value_bank(fundamentals)

    assert validated == [(result.bear, result.base, result.bull)]
