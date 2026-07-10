import math
from dataclasses import dataclass
from statistics import median

from app.services.valuation_math import validate_scenarios
from app.services.valuation_types import (
    APPROVED_BANK_METRIC_KEYS,
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)


_PROJECTION_YEARS = 10
_SCENARIO_INPUTS = {
    "bear": (0.90, 0.12),
    "base": (1.00, 0.10),
    "bull": (1.05, 0.08),
}


@dataclass(frozen=True)
class BankNormalizedInputs:
    common_equity: float
    diluted_shares: float
    normalized_roe: float
    payout_ratio: float
    book_value_per_share: float
    usable_years: int

    def __post_init__(self) -> None:
        positive_values = {
            "common equity": self.common_equity,
            "diluted shares": self.diluted_shares,
            "normalized ROE": self.normalized_roe,
            "book value per share": self.book_value_per_share,
        }
        for name, value in positive_values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if (
            not math.isfinite(self.payout_ratio)
            or not 0 <= self.payout_ratio <= 1
        ):
            raise ValueError("payout ratio must be finite and between 0 and 1")
        if self.usable_years < 3:
            raise ValueError("bank normalization requires three valid observations")


def _select_annual_periods(
    fundamentals: ValuationFundamentals,
) -> list[FinancialPeriod]:
    annual_by_year: dict[int, tuple[int, FinancialPeriod]] = {}
    for input_index, period in enumerate(fundamentals.periods):
        if period.is_ttm:
            continue
        year_key = (
            period.fiscal_year
            if period.fiscal_year is not None
            else period.period_end.year
        )
        current = annual_by_year.get(year_key)
        if current is None or (period.period_end, input_index) >= (
            current[1].period_end,
            current[0],
        ):
            annual_by_year[year_key] = (input_index, period)
    return sorted(
        (period for _, period in annual_by_year.values()),
        key=lambda period: period.period_end,
    )


def _validate_periods(
    annual_periods: list[FinancialPeriod], valuation_currency: str
) -> None:
    for period in annual_periods:
        if period.currency.strip().upper() != valuation_currency:
            raise ValueError("bank history must use the valuation currency")
        for value in (
            period.common_equity,
            period.net_income_common,
            period.common_dividends,
            period.diluted_shares,
        ):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("bank history values must be finite")
        if period.common_equity is not None and period.common_equity <= 0:
            raise ValueError("bank valuation requires positive common equity")
        if period.diluted_shares is not None and period.diluted_shares <= 0:
            raise ValueError("bank history requires positive diluted shares")


def normalize_bank_history(
    fundamentals: ValuationFundamentals,
) -> BankNormalizedInputs:
    annual_periods = _select_annual_periods(fundamentals)
    if not annual_periods:
        raise ValueError("bank valuation requires annual history")

    currency = fundamentals.currency.strip().upper()
    _validate_periods(annual_periods, currency)

    current_equity = annual_periods[-1].common_equity
    if current_equity is None or current_equity <= 0:
        raise ValueError("bank valuation requires positive common equity")
    current_shares = fundamentals.current_diluted_shares
    if (
        current_shares is None
        or not math.isfinite(float(current_shares))
        or current_shares <= 0
    ):
        raise ValueError("bank valuation requires positive diluted shares")

    observations: list[tuple[float, float]] = []
    for beginning, current in zip(annual_periods, annual_periods[1:]):
        required = (
            beginning.common_equity,
            current.common_equity,
            current.net_income_common,
            current.common_dividends,
            current.diluted_shares,
        )
        if any(value is None for value in required):
            continue
        net_income = float(current.net_income_common)
        if net_income <= 0:
            continue
        average_equity = (
            float(beginning.common_equity) + float(current.common_equity)
        ) / 2.0
        roe = net_income / average_equity
        payout_ratio = abs(float(current.common_dividends)) / net_income
        if not 0 <= payout_ratio <= 1:
            raise ValueError("bank payout ratio must be between 0 and 1")
        if not math.isfinite(roe) or roe <= 0:
            continue
        observations.append((roe, payout_ratio))

    if len(observations) < 3:
        raise ValueError("bank normalization requires three valid observations")

    recent_observations = observations[-5:]
    common_equity = float(current_equity)
    diluted_shares = float(current_shares)
    return BankNormalizedInputs(
        common_equity=common_equity,
        diluted_shares=diluted_shares,
        normalized_roe=float(median(roe for roe, _ in recent_observations)),
        payout_ratio=float(
            median(payout for _, payout in recent_observations)
        ),
        book_value_per_share=common_equity / diluted_shares,
        usable_years=len(observations),
    )


def _project_scenario(
    normalized: BankNormalizedInputs,
    *,
    starting_roe_factor: float,
    required_return: float,
) -> tuple[float, list[float]]:
    for value in (starting_roe_factor, required_return):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                "bank scenario assumptions must be finite and positive"
            )

    beginning_equity = normalized.common_equity
    starting_roe = normalized.normalized_roe * starting_roe_factor
    present_value_excess_returns = 0.0
    projected_book_equity: list[float] = []
    for year in range(1, _PROJECTION_YEARS + 1):
        progress = (year - 1) / (_PROJECTION_YEARS - 1)
        projected_roe = (
            required_return
            if year == _PROJECTION_YEARS
            else starting_roe + (required_return - starting_roe) * progress
        )
        net_income = beginning_equity * projected_roe
        dividends = normalized.payout_ratio * net_income
        excess_return = (projected_roe - required_return) * beginning_equity
        discounted_excess_return = excess_return / (
            (1.0 + required_return) ** year
        )
        ending_equity = beginning_equity + net_income - dividends
        projected_values = (
            projected_roe,
            net_income,
            dividends,
            excess_return,
            discounted_excess_return,
            ending_equity,
        )
        if any(not math.isfinite(value) for value in projected_values):
            raise ValueError("bank scenario projection must remain finite")
        if ending_equity <= 0:
            raise ValueError("projected common equity must remain positive")
        present_value_excess_returns += discounted_excess_return
        projected_book_equity.append(ending_equity)
        beginning_equity = ending_equity

    intrinsic_common_equity = (
        normalized.common_equity + present_value_excess_returns
    )
    if not math.isfinite(intrinsic_common_equity):
        raise ValueError("bank intrinsic common equity must be finite")
    return (
        round(intrinsic_common_equity / normalized.diluted_shares, 4),
        projected_book_equity,
    )


def value_bank(fundamentals: ValuationFundamentals) -> ModelResult:
    normalized = normalize_bank_history(fundamentals)
    scenario_values: dict[str, float] = {}
    projected_book_equity: dict[str, list[float]] = {}
    for name, (starting_roe_factor, required_return) in _SCENARIO_INPUTS.items():
        value, projected_equity = _project_scenario(
            normalized,
            starting_roe_factor=starting_roe_factor,
            required_return=required_return,
        )
        scenario_values[name] = value
        projected_book_equity[name] = projected_equity

    validate_scenarios(
        scenario_values["bear"],
        scenario_values["base"],
        scenario_values["bull"],
    )

    missing_bank_metrics = sorted(
        APPROVED_BANK_METRIC_KEYS - fundamentals.bank_metrics.keys()
    )
    details: dict[str, object] = {
        "method": "bank_residual_income",
        "normalized_roe": normalized.normalized_roe,
        "book_value_per_share": normalized.book_value_per_share,
        "payout_ratio": normalized.payout_ratio,
        "usable_years": normalized.usable_years,
        "projected_book_equity": projected_book_equity,
    }
    details.update(
        {
            metric: fundamentals.bank_metrics.get(metric)
            for metric in sorted(APPROVED_BANK_METRIC_KEYS)
        }
    )
    return ModelResult(
        method="bank_residual_income",
        detected_company_type="bank",
        bear=scenario_values["bear"],
        base=scenario_values["base"],
        bull=scenario_values["bull"],
        details=details,
        assumptions={
            "projection_years": _PROJECTION_YEARS,
            "margin_of_safety": 0.25,
            "terminal_excess_return": 0.0,
            "scenarios": {
                name: {
                    "starting_roe_factor": starting_roe_factor,
                    "required_return": required_return,
                }
                for name, (
                    starting_roe_factor,
                    required_return,
                ) in _SCENARIO_INPUTS.items()
            },
        },
        quality={
            "eligible": True,
            "reasons": [],
            "details": {
                "usable_years": normalized.usable_years,
                "available_bank_metrics": sorted(fundamentals.bank_metrics),
                "missing_bank_metrics": missing_bank_metrics,
            },
        },
        warnings=list(fundamentals.warnings),
    )
