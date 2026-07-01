from app.core.config import get_settings
from app.schemas import (
    StockSnapshot,
    ValuationAssumptions,
    ValuationRatios,
    ValuationRequest,
    ValuationResponse,
)


def build_valuation(
    snapshot: StockSnapshot,
    overrides: ValuationRequest | None = None,
) -> ValuationResponse:
    assumptions = _resolve_assumptions(overrides or ValuationRequest())
    quote = snapshot.quote
    financials = snapshot.financials
    warnings = _dedupe([*snapshot.warnings, *quote.warnings])

    equity_value = None
    enterprise_value = None
    intrinsic_value = None
    margin_price = None
    upside_downside = None

    if assumptions.discount_rate <= assumptions.terminal_growth_rate:
        warnings.append("Discount rate must be greater than terminal growth rate for DCF.")
    elif financials.free_cash_flow is None:
        warnings.append("Free cash flow is missing; DCF intrinsic value cannot be calculated.")
    elif quote.shares_outstanding in (None, 0):
        warnings.append("Shares outstanding is missing; per-share intrinsic value cannot be calculated.")
    else:
        enterprise_value = discounted_cash_flow_value(
            free_cash_flow=financials.free_cash_flow,
            assumptions=assumptions,
        )
        equity_value = enterprise_value - (financials.total_debt or 0) + (
            financials.cash_and_equivalents or 0
        )
        intrinsic_value = equity_value / quote.shares_outstanding
        margin_price = intrinsic_value * (1 - assumptions.margin_of_safety)

        if quote.current_price not in (None, 0):
            upside_downside = ((intrinsic_value - quote.current_price) / quote.current_price) * 100
        else:
            warnings.append("Current price is missing; upside/downside cannot be calculated.")

    ratios = ValuationRatios(
        trailing_pe=quote.trailing_pe,
        forward_pe=quote.forward_pe,
        price_to_book=quote.price_to_book,
        enterprise_to_ebitda=quote.enterprise_to_ebitda,
        dividend_yield=quote.dividend_yield,
        market_cap_to_free_cash_flow=_safe_divide(quote.market_cap, financials.free_cash_flow),
    )

    return ValuationResponse(
        symbol=snapshot.symbol,
        currency=quote.currency,
        current_price=quote.current_price,
        intrinsic_value_per_share=_round_money(intrinsic_value),
        margin_of_safety_price=_round_money(margin_price),
        upside_downside_percent=_round_percent(upside_downside),
        enterprise_value=_round_money(enterprise_value),
        equity_value=_round_money(equity_value),
        assumptions=assumptions,
        ratios=ratios,
        warnings=_dedupe(warnings),
    )


def discounted_cash_flow_value(
    free_cash_flow: float,
    assumptions: ValuationAssumptions,
) -> float:
    projected_cash_flows = [
        free_cash_flow * ((1 + assumptions.growth_rate) ** year)
        for year in range(1, assumptions.projection_years + 1)
    ]
    discounted_cash_flows = [
        cash_flow / ((1 + assumptions.discount_rate) ** year)
        for year, cash_flow in enumerate(projected_cash_flows, start=1)
    ]

    final_cash_flow = projected_cash_flows[-1]
    terminal_value = final_cash_flow * (1 + assumptions.terminal_growth_rate) / (
        assumptions.discount_rate - assumptions.terminal_growth_rate
    )
    discounted_terminal_value = terminal_value / (
        (1 + assumptions.discount_rate) ** assumptions.projection_years
    )

    return sum(discounted_cash_flows) + discounted_terminal_value


def _resolve_assumptions(overrides: ValuationRequest) -> ValuationAssumptions:
    settings = get_settings()
    return ValuationAssumptions(
        discount_rate=overrides.discount_rate
        if overrides.discount_rate is not None
        else settings.default_discount_rate,
        terminal_growth_rate=overrides.terminal_growth_rate
        if overrides.terminal_growth_rate is not None
        else settings.default_terminal_growth_rate,
        projection_years=overrides.projection_years
        if overrides.projection_years is not None
        else settings.default_projection_years,
        margin_of_safety=overrides.margin_of_safety
        if overrides.margin_of_safety is not None
        else settings.default_margin_of_safety,
        growth_rate=overrides.growth_rate
        if overrides.growth_rate is not None
        else settings.default_growth_rate,
    )


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _round_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(messages))
