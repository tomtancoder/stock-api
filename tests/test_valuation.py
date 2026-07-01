from app.schemas import FinancialMetrics, QuoteResponse, StockSnapshot, ValuationRequest
from app.services.valuation import build_valuation, discounted_cash_flow_value


def test_discounted_cash_flow_value_uses_projected_cash_flows():
    assumptions = build_valuation(
        snapshot=_snapshot(free_cash_flow=100, shares=10, price=50),
        overrides=ValuationRequest(
            discount_rate=0.10,
            terminal_growth_rate=0.02,
            projection_years=5,
            margin_of_safety=0.25,
            growth_rate=0.03,
        ),
    ).assumptions

    value = discounted_cash_flow_value(free_cash_flow=100, assumptions=assumptions)

    assert round(value, 2) == 1330.04


def test_build_valuation_returns_intrinsic_value_and_ratios():
    response = build_valuation(
        snapshot=_snapshot(
            free_cash_flow=1_000,
            shares=100,
            price=80,
            market_cap=8_000,
            total_debt=500,
            cash=250,
        ),
        overrides=ValuationRequest(
            discount_rate=0.10,
            terminal_growth_rate=0.02,
            projection_years=5,
            margin_of_safety=0.25,
            growth_rate=0.03,
        ),
    )

    assert response.intrinsic_value_per_share == 130.5
    assert response.margin_of_safety_price == 97.88
    assert response.upside_downside_percent == 63.13
    assert response.ratios.market_cap_to_free_cash_flow == 8
    assert response.warnings == []


def test_build_valuation_warns_when_free_cash_flow_is_missing():
    response = build_valuation(
        snapshot=_snapshot(free_cash_flow=None, shares=100, price=80),
        overrides=ValuationRequest(),
    )

    assert response.intrinsic_value_per_share is None
    assert "Free cash flow is missing; DCF intrinsic value cannot be calculated." in response.warnings


def _snapshot(
    free_cash_flow: float | None,
    shares: float | None,
    price: float | None,
    market_cap: float | None = None,
    total_debt: float | None = None,
    cash: float | None = None,
) -> StockSnapshot:
    return StockSnapshot(
        symbol="ACME",
        quote=QuoteResponse(
            symbol="ACME",
            currency="USD",
            current_price=price,
            market_cap=market_cap,
            shares_outstanding=shares,
            trailing_pe=20,
            forward_pe=18,
            price_to_book=4,
            enterprise_to_ebitda=12,
            dividend_yield=0.01,
        ),
        financials=FinancialMetrics(
            free_cash_flow=free_cash_flow,
            total_debt=total_debt,
            cash_and_equivalents=cash,
        ),
    )
