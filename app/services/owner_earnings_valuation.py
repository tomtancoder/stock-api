import math
from statistics import median

from app.services.valuation_math import validate_scenarios
from app.services.valuation_types import (
    FinancialPeriod,
    ModelResult,
    ScenarioAssumptions,
    ValuationFundamentals,
)


def calculate_period_owner_earnings(period: FinancialPeriod) -> float | None:
    required = (period.operating_cash_flow, period.capital_expenditure)
    if any(value is None for value in required):
        return None
    if period.stock_based_compensation is None:
        return None
    if period.interest_paid_outside_operating is None:
        return None
    return (
        float(period.operating_cash_flow)
        - abs(float(period.capital_expenditure))
        - abs(float(period.stock_based_compensation))
        - abs(float(period.interest_paid_outside_operating))
    )


def _positive_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value)) and value > 0


def _select_periods(
    fundamentals: ValuationFundamentals,
) -> tuple[list[FinancialPeriod], FinancialPeriod | None]:
    annual_by_year: dict[int, tuple[int, FinancialPeriod]] = {}
    trailing_candidates: list[tuple[int, FinancialPeriod]] = []
    for input_index, period in enumerate(fundamentals.periods):
        if period.is_ttm:
            trailing_candidates.append((input_index, period))
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

    annual_periods = sorted(
        (period for _, period in annual_by_year.values()),
        key=lambda period: period.period_end,
    )
    latest_trailing = (
        max(
            trailing_candidates,
            key=lambda candidate: (candidate[1].period_end, candidate[0]),
        )[1]
        if trailing_candidates
        else None
    )
    return annual_periods, latest_trailing


def _normalize_owner_earnings(
    fundamentals: ValuationFundamentals,
    annual_periods: list[FinancialPeriod],
    trailing_period: FinancialPeriod | None,
) -> float:
    currency = fundamentals.currency.strip().upper()
    if any(
        period.currency.strip().upper() != currency
        for period in [
            *annual_periods,
            *([trailing_period] if trailing_period is not None else []),
        ]
    ):
        raise ValueError("owner earnings periods must use the valuation currency")

    annual_owner_earnings = []
    for period in annual_periods:
        owner_earnings = calculate_period_owner_earnings(period)
        if _positive_finite(owner_earnings):
            annual_owner_earnings.append((period, float(owner_earnings)))

    if len(annual_owner_earnings) < 3:
        raise ValueError("owner earnings require three positive annual periods")

    components = []
    latest_three = annual_owner_earnings[-3:]
    components.append(
        sum(
            weight * owner_earnings
            for weight, (_, owner_earnings) in zip(
                (1, 2, 3), latest_three
            )
        )
        / 6.0
    )

    trailing_owner_earnings = (
        calculate_period_owner_earnings(trailing_period)
        if trailing_period is not None
        else None
    )
    if _positive_finite(trailing_owner_earnings):
        components.append(float(trailing_owner_earnings))

    margin_history = [
        owner_earnings / float(period.revenue)
        for period, owner_earnings in annual_owner_earnings
        if _positive_finite(period.revenue)
    ]
    trailing_revenue = (
        float(trailing_period.revenue)
        if trailing_period is not None
        and _positive_finite(trailing_period.revenue)
        else None
    )
    if len(margin_history) >= 5 and trailing_revenue is not None:
        components.append(median(margin_history[-5:]) * trailing_revenue)

    if len(components) < 2:
        raise ValueError(
            "owner earnings normalization requires two independent components"
        )
    return float(median(components))


def normalize_owner_earnings(fundamentals: ValuationFundamentals) -> float:
    annual_periods, trailing_period = _select_periods(fundamentals)
    return _normalize_owner_earnings(
        fundamentals, annual_periods, trailing_period
    )


def _cagr_candidate(
    observations: list[tuple[FinancialPeriod, float]],
) -> float | None:
    if len(observations) < 2:
        return None
    earliest_period, earliest_value = observations[0]
    latest_period, latest_value = observations[-1]
    elapsed_days = (latest_period.period_end - earliest_period.period_end).days
    elapsed_years = elapsed_days / 365.25
    if elapsed_years <= 0:
        return None
    candidate = (latest_value / earliest_value) ** (1.0 / elapsed_years) - 1.0
    return candidate if math.isfinite(candidate) else None


def _derive_growth(annual_periods: list[FinancialPeriod]) -> float:
    revenue_per_share = [
        (period, float(period.revenue) / float(period.diluted_shares))
        for period in annual_periods
        if _positive_finite(period.revenue)
        and _positive_finite(period.diluted_shares)
    ]
    owner_earnings_per_share = []
    for period in annual_periods:
        owner_earnings = calculate_period_owner_earnings(period)
        if _positive_finite(owner_earnings) and _positive_finite(
            period.diluted_shares
        ):
            owner_earnings_per_share.append(
                (period, float(owner_earnings) / float(period.diluted_shares))
            )

    candidates = [
        candidate
        for observations in (revenue_per_share, owner_earnings_per_share)
        if (candidate := _cagr_candidate(observations)) is not None
    ]
    if not candidates:
        raise ValueError(
            "owner earnings growth requires comparable per-share history"
        )
    return max(-0.15, min(0.12, float(median(candidates))))


def _scenario_value(
    normalized_owner_earnings: float,
    diluted_shares: float,
    scenario: ScenarioAssumptions,
) -> float:
    if scenario.required_return <= scenario.terminal_growth:
        raise ValueError("required return must exceed terminal growth")

    projected_owner_earnings = normalized_owner_earnings * scenario.starting_factor
    present_value = 0.0
    for year in range(1, scenario.projection_years + 1):
        fade_fraction = (
            (year - 1) / (scenario.projection_years - 1)
            if scenario.projection_years > 1
            else 1.0
        )
        growth = scenario.initial_growth + (
            scenario.terminal_growth - scenario.initial_growth
        ) * fade_fraction
        projected_owner_earnings *= 1.0 + growth
        present_value += projected_owner_earnings / (
            (1.0 + scenario.required_return) ** year
        )

    terminal_value = (
        projected_owner_earnings
        * (1.0 + scenario.terminal_growth)
        / (scenario.required_return - scenario.terminal_growth)
    )
    present_value += terminal_value / (
        (1.0 + scenario.required_return) ** scenario.projection_years
    )
    return round(present_value / diluted_shares, 4)


def _annual_history(
    annual_periods: list[FinancialPeriod],
) -> list[dict[str, object]]:
    history = []
    for period in annual_periods:
        owner_earnings = calculate_period_owner_earnings(period)
        if owner_earnings is None or not math.isfinite(owner_earnings):
            continue
        history.append(
            {
                "period_end": period.period_end,
                "currency": period.currency,
                "operating_cash_flow": float(period.operating_cash_flow),
                "maintenance_capex": abs(float(period.capital_expenditure)),
                "maintenance_capex_method": "total_capital_expenditure",
                "stock_based_compensation": abs(
                    float(period.stock_based_compensation)
                ),
                "interest_paid_outside_operating": abs(
                    float(period.interest_paid_outside_operating)
                ),
                "owner_earnings": float(owner_earnings),
            }
        )
    return history


def value_owner_earnings(fundamentals: ValuationFundamentals) -> ModelResult:
    if not _positive_finite(fundamentals.current_diluted_shares):
        raise ValueError("owner earnings valuation requires positive diluted shares")

    annual_periods, trailing_period = _select_periods(fundamentals)
    normalized_owner_earnings = _normalize_owner_earnings(
        fundamentals, annual_periods, trailing_period
    )
    base_growth = _derive_growth(annual_periods)
    bear_growth = max(-0.20, base_growth - 0.04)
    bull_growth = min(0.15, base_growth + 0.03)
    scenarios = (
        ScenarioAssumptions(
            name="bear",
            starting_factor=0.90,
            initial_growth=bear_growth,
            required_return=0.12,
            terminal_growth=0.02,
        ),
        ScenarioAssumptions(
            name="base",
            starting_factor=1.00,
            initial_growth=base_growth,
            required_return=0.10,
            terminal_growth=0.025,
        ),
        ScenarioAssumptions(
            name="bull",
            starting_factor=1.05,
            initial_growth=bull_growth,
            required_return=0.08,
            terminal_growth=0.03,
        ),
    )
    diluted_shares = float(fundamentals.current_diluted_shares)
    scenario_values = {
        scenario.name: _scenario_value(
            normalized_owner_earnings, diluted_shares, scenario
        )
        for scenario in scenarios
    }
    validate_scenarios(
        scenario_values["bear"],
        scenario_values["base"],
        scenario_values["bull"],
    )

    history = _annual_history(annual_periods)
    usable_years = sum(entry["owner_earnings"] > 0 for entry in history)
    return ModelResult(
        method="owner_earnings_dcf",
        detected_company_type="operating_company",
        bear=scenario_values["bear"],
        base=scenario_values["base"],
        bull=scenario_values["bull"],
        details={
            "method": "owner_earnings_dcf",
            "normalized_owner_earnings": normalized_owner_earnings,
            "owner_earnings_per_share": normalized_owner_earnings / diluted_shares,
            "maintenance_capex_method": "total_capital_expenditure",
            "annual_history": history,
            "derived_growth": base_growth,
            "usable_years": usable_years,
        },
        assumptions={
            "projection_years": 10,
            "margin_of_safety": 0.25,
            "scenarios": {
                scenario.name: scenario.model_dump(exclude={"name"})
                for scenario in scenarios
            },
        },
        quality={
            "eligible": True,
            "reasons": [],
            "details": {"usable_years": usable_years},
        },
        warnings=list(fundamentals.warnings),
    )
