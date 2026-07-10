import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from statistics import median

from app.services.valuation_math import validate_scenarios
from app.services.valuation_types import (
    APPROVED_REIT_METRIC_KEYS,
    FinancialPeriod,
    ModelResult,
    ValuationFundamentals,
)


_PROJECTION_YEARS = 10
_DPU_GROWTH_BOUNDS = (-0.03, 0.03)
_NAV_GROWTH_BOUNDS = (-0.02, 0.025)
_SCENARIO_INPUTS = {
    "bear": (0.90, 0.10, 0.85),
    "base": (1.00, 0.085, 1.00),
    "bull": (1.05, 0.07, 1.10),
}
_TERMINAL_DISTRIBUTION_GROWTH = {
    "bear": 0.0,
    "base": 0.015,
    "bull": 0.025,
}


def _is_finite_real(value: object) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class ReitNormalizedInputs:
    normalized_dpu: float
    current_nav_per_unit: float | None
    base_dpu_growth: float
    base_nav_growth: float
    usable_years: int

    def __post_init__(self) -> None:
        if not _is_finite_real(self.normalized_dpu) or self.normalized_dpu <= 0:
            raise ValueError("normalized DPU must be finite and positive")
        if self.current_nav_per_unit is not None and (
            not _is_finite_real(self.current_nav_per_unit)
            or self.current_nav_per_unit <= 0
        ):
            raise ValueError("current NAV per unit must be finite and positive")
        if (
            not _is_finite_real(self.base_dpu_growth)
            or not _DPU_GROWTH_BOUNDS[0]
            <= self.base_dpu_growth
            <= _DPU_GROWTH_BOUNDS[1]
        ):
            raise ValueError("DPU growth must be finite and within approved bounds")
        if (
            not _is_finite_real(self.base_nav_growth)
            or not _NAV_GROWTH_BOUNDS[0]
            <= self.base_nav_growth
            <= _NAV_GROWTH_BOUNDS[1]
        ):
            raise ValueError("NAV growth must be finite and within approved bounds")
        if type(self.usable_years) is not int or self.usable_years < 3:
            raise ValueError("usable years must be an integer of at least three")


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
    trailing_period = (
        max(
            trailing_candidates,
            key=lambda candidate: (candidate[1].period_end, candidate[0]),
        )[1]
        if trailing_candidates
        else None
    )
    return annual_periods, trailing_period


def _validate_periods(
    periods: list[FinancialPeriod], valuation_currency: str
) -> None:
    for period in periods:
        if period.currency.strip().upper() != valuation_currency:
            raise ValueError("REIT history must use the valuation currency")
        for value in (period.distribution_per_unit, period.nav_per_unit):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("REIT history values must be finite")


def _positive_observations(
    periods: list[FinancialPeriod], field: str
) -> list[tuple[FinancialPeriod, float]]:
    observations: list[tuple[FinancialPeriod, float]] = []
    for period in periods:
        value = getattr(period, field)
        if value is not None and value > 0:
            observations.append((period, float(value)))
    return observations


def _cagr(observations: list[tuple[FinancialPeriod, float]]) -> float | None:
    if len(observations) < 2:
        return None
    earliest_period, earliest_value = observations[0]
    latest_period, latest_value = observations[-1]
    elapsed_years = (
        latest_period.period_end - earliest_period.period_end
    ).days / 365.25
    if elapsed_years <= 0:
        return None
    growth = (latest_value / earliest_value) ** (1.0 / elapsed_years) - 1.0
    return growth if math.isfinite(growth) else None


def _require_current_units(fundamentals: ValuationFundamentals) -> None:
    units = fundamentals.current_diluted_shares
    if not _is_finite_real(units) or units <= 0:
        raise ValueError("REIT valuation requires positive current units")


def normalize_reit_history(
    fundamentals: ValuationFundamentals,
) -> ReitNormalizedInputs:
    _require_current_units(fundamentals)
    annual_periods, trailing_period = _select_periods(fundamentals)
    if not annual_periods:
        raise ValueError("REIT valuation requires annual DPU history")
    valuation_currency = fundamentals.currency.strip().upper()
    _validate_periods(
        [
            *annual_periods,
            *([trailing_period] if trailing_period is not None else []),
        ],
        valuation_currency,
    )

    dpu_observations = _positive_observations(
        annual_periods, "distribution_per_unit"
    )
    if len(dpu_observations) < 3:
        raise ValueError("REIT normalization requires three usable DPU years")

    dpu_components = [
        sum(
            weight * dpu
            for weight, (_, dpu) in zip((1, 2, 3), dpu_observations[-3:])
        )
        / 6.0,
        float(median(dpu for _, dpu in dpu_observations[-5:])),
    ]
    if (
        trailing_period is not None
        and trailing_period.distribution_per_unit is not None
        and trailing_period.distribution_per_unit > 0
    ):
        dpu_components.append(float(trailing_period.distribution_per_unit))
    if len(dpu_components) < 2:
        raise ValueError(
            "REIT normalization requires two independent DPU components"
        )

    dpu_cagr = _cagr(dpu_observations)
    if dpu_cagr is None:
        raise ValueError("REIT DPU growth requires comparable annual history")
    nav_observations = _positive_observations(annual_periods, "nav_per_unit")
    nav_cagr = _cagr(nav_observations)
    return ReitNormalizedInputs(
        normalized_dpu=float(median(dpu_components)),
        current_nav_per_unit=(
            nav_observations[-1][1] if nav_observations else None
        ),
        base_dpu_growth=_clamp(dpu_cagr, *_DPU_GROWTH_BOUNDS),
        base_nav_growth=(
            _clamp(nav_cagr, *_NAV_GROWTH_BOUNDS)
            if nav_cagr is not None
            else 0.0
        ),
        usable_years=len(dpu_observations),
    )


def _scenario_inputs(normalized: ReitNormalizedInputs) -> dict[str, dict[str, float]]:
    inputs: dict[str, dict[str, float]] = {}
    for name, (starting_dpu_factor, required_return, terminal_nav_factor) in (
        _SCENARIO_INPUTS.items()
    ):
        dpu_growth = (
            max(-0.05, normalized.base_dpu_growth - 0.02)
            if name == "bear"
            else min(0.05, normalized.base_dpu_growth + 0.02)
            if name == "bull"
            else normalized.base_dpu_growth
        )
        nav_growth = (
            max(_NAV_GROWTH_BOUNDS[0], normalized.base_nav_growth - 0.01)
            if name == "bear"
            else min(_NAV_GROWTH_BOUNDS[1], normalized.base_nav_growth + 0.01)
            if name == "bull"
            else normalized.base_nav_growth
        )
        inputs[name] = {
            "starting_dpu_factor": starting_dpu_factor,
            "dpu_growth": dpu_growth,
            "required_return": required_return,
            "nav_growth": nav_growth,
            "terminal_nav_factor": terminal_nav_factor,
        }
    return inputs


def _project_distributions(
    normalized_dpu: float, *, starting_factor: float, growth: float, required_return: float
) -> tuple[float, float]:
    dpu = normalized_dpu * starting_factor
    present_value = 0.0
    for year in range(1, _PROJECTION_YEARS + 1):
        dpu *= 1.0 + growth
        discounted_dpu = dpu / ((1.0 + required_return) ** year)
        if not math.isfinite(discounted_dpu) or discounted_dpu <= 0:
            raise ValueError("REIT distribution projection must remain positive and finite")
        present_value += discounted_dpu
    return present_value, dpu


def _present_value_terminal_nav(
    current_nav_per_unit: float, *, growth: float, terminal_factor: float, required_return: float
) -> float:
    terminal_nav = (
        current_nav_per_unit
        * ((1.0 + growth) ** _PROJECTION_YEARS)
        * terminal_factor
    )
    present_value = terminal_nav / ((1.0 + required_return) ** _PROJECTION_YEARS)
    if not math.isfinite(present_value) or present_value <= 0:
        raise ValueError("REIT terminal NAV must be positive and finite")
    return present_value


def _present_value_terminal_distribution(
    dpu_year_ten: float, *, terminal_growth: float, required_return: float
) -> float:
    if required_return <= terminal_growth:
        raise ValueError("required return must exceed terminal growth")
    terminal_value = (
        dpu_year_ten
        * (1.0 + terminal_growth)
        / (required_return - terminal_growth)
    )
    present_value = terminal_value / ((1.0 + required_return) ** _PROJECTION_YEARS)
    if not math.isfinite(present_value) or present_value <= 0:
        raise ValueError("REIT terminal distribution value must be positive and finite")
    return present_value


def _sanitize_reit_metrics(metrics: object) -> tuple[dict[str, float], list[str]]:
    if not isinstance(metrics, Mapping):
        return {}, ["reit_metrics"]
    valid_metrics: dict[str, float] = {}
    ignored_metrics: list[str] = []
    for key, value in metrics.items():
        if key not in APPROVED_REIT_METRIC_KEYS or not _is_finite_real(value):
            ignored_metrics.append(str(key))
            continue
        valid_metrics[key] = float(value)
    return valid_metrics, sorted(set(ignored_metrics))


def _unreliable(reasons: list[str]):
    # Import lazily: valuation_router imports this engine when REIT dispatch is enabled.
    from app.services.valuation_router import ValuationUnreliable

    return ValuationUnreliable(reasons)


def value_reit(fundamentals: ValuationFundamentals) -> ModelResult:
    try:
        normalized = normalize_reit_history(fundamentals)
    except ValueError as exc:
        raise _unreliable([str(exc)]) from exc

    scenario_inputs = _scenario_inputs(normalized)
    scenario_values: dict[str, float] = {}
    scenario_contributions: dict[str, dict[str, float]] = {}
    distribution_only = normalized.current_nav_per_unit is None
    for name, assumptions in scenario_inputs.items():
        pv_distributions, dpu_year_ten = _project_distributions(
            normalized.normalized_dpu,
            starting_factor=assumptions["starting_dpu_factor"],
            growth=assumptions["dpu_growth"],
            required_return=assumptions["required_return"],
        )
        if distribution_only:
            terminal_growth = _TERMINAL_DISTRIBUTION_GROWTH[name]
            pv_terminal_value = _present_value_terminal_distribution(
                dpu_year_ten,
                terminal_growth=terminal_growth,
                required_return=assumptions["required_return"],
            )
            scenario_contributions[name] = {
                "pv_distributions": pv_distributions,
                "pv_terminal_value": pv_terminal_value,
            }
            scenario_values[name] = round(
                pv_distributions + pv_terminal_value, 4
            )
            assumptions["terminal_growth"] = terminal_growth
            continue
        pv_terminal_nav = _present_value_terminal_nav(
            normalized.current_nav_per_unit,
            growth=assumptions["nav_growth"],
            terminal_factor=assumptions["terminal_nav_factor"],
            required_return=assumptions["required_return"],
        )
        scenario_contributions[name] = {
            "pv_distributions": pv_distributions,
            "pv_terminal_nav": pv_terminal_nav,
        }
        scenario_values[name] = round(pv_distributions + pv_terminal_nav, 4)

    validate_scenarios(
        scenario_values["bear"],
        scenario_values["base"],
        scenario_values["bull"],
    )

    reit_metrics, ignored_reit_metrics = _sanitize_reit_metrics(
        fundamentals.reit_metrics
    )
    missing_reit_metrics = sorted(
        APPROVED_REIT_METRIC_KEYS - reit_metrics.keys()
    )
    details: dict[str, object] = {
        "method": (
            "reit_distribution_only"
            if distribution_only
            else "reit_distribution_nav"
        ),
        "normalized_dpu": normalized.normalized_dpu,
        "nav_per_unit": normalized.current_nav_per_unit,
        "price_to_nav": None,
        "distribution_yield": None,
        "usable_years": normalized.usable_years,
        "base_dpu_growth": normalized.base_dpu_growth,
        "base_nav_growth": normalized.base_nav_growth,
        "present_value_distributions": {
            name: contribution["pv_distributions"]
            for name, contribution in scenario_contributions.items()
        },
        "present_value_terminal": {
            name: contribution.get(
                "pv_terminal_value", contribution.get("pv_terminal_nav")
            )
            for name, contribution in scenario_contributions.items()
        },
        "scenario_contributions": scenario_contributions,
    }
    details.update(
        {
            metric: reit_metrics.get(metric)
            for metric in sorted(APPROVED_REIT_METRIC_KEYS)
        }
    )
    warnings = list(fundamentals.warnings)
    if distribution_only:
        warnings.append(
            "NAV per unit is unavailable; used lower-confidence distribution-only valuation."
        )
    if missing_reit_metrics:
        warnings.append(
            "Missing optional REIT metrics: "
            f"{', '.join(missing_reit_metrics)}."
        )
    if ignored_reit_metrics:
        warnings.append(
            "Ignored invalid optional REIT metrics: "
            f"{', '.join(ignored_reit_metrics)}."
        )
    quality: dict[str, object] = {
        "eligible": True,
        "reasons": [],
        "details": {
            "usable_years": normalized.usable_years,
            "distribution_only": distribution_only,
            "available_reit_metrics": sorted(reit_metrics),
            "missing_reit_metrics": missing_reit_metrics,
            "ignored_reit_metrics": ignored_reit_metrics,
        },
    }
    if distribution_only:
        quality["confidence"] = "low"
    elif missing_reit_metrics or ignored_reit_metrics:
        quality["confidence"] = "medium"
    return ModelResult(
        method=details["method"],
        detected_company_type="reit",
        bear=scenario_values["bear"],
        base=scenario_values["base"],
        bull=scenario_values["bull"],
        details=details,
        assumptions={
            "projection_years": _PROJECTION_YEARS,
            "margin_of_safety": 0.25,
            "scenarios": scenario_inputs,
        },
        quality=quality,
        warnings=warnings,
    )
