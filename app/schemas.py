from pydantic import BaseModel, ConfigDict, Field


class QuoteResponse(BaseModel):
    symbol: str
    short_name: str | None = None
    long_name: str | None = None
    exchange: str | None = None
    currency: str | None = None
    current_price: float | None = None
    previous_close: float | None = None
    price_change: float | None = None
    price_change_percent: float | None = None
    volume: float | None = None
    average_volume: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    enterprise_to_ebitda: float | None = None
    dividend_yield: float | None = None
    warnings: list[str] = Field(default_factory=list)


class FinancialMetrics(BaseModel):
    revenue: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditures: float | None = None
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    total_equity: float | None = None


class FundamentalsResponse(BaseModel):
    symbol: str
    currency: str | None = None
    financials: FinancialMetrics
    shares_outstanding: float | None = None
    warnings: list[str] = Field(default_factory=list)


class EmaValues(BaseModel):
    ema_21: float | None = None
    ema_50: float | None = None
    ema_100: float | None = None
    ema_200: float | None = None


class TechnicalsResponse(BaseModel):
    symbol: str
    period: str
    interval: str
    as_of: str | None = None
    latest_close: float | None = None
    ema: EmaValues
    warnings: list[str] = Field(default_factory=list)


class ValuationRequest(BaseModel):
    discount_rate: float | None = Field(default=None, gt=0, lt=1)
    terminal_growth_rate: float | None = Field(default=None, ge=0, lt=0.10)
    projection_years: int | None = Field(default=None, ge=1, le=10)
    margin_of_safety: float | None = Field(default=None, ge=0, lt=1)
    growth_rate: float | None = Field(default=None, ge=-0.50, lt=1)


class ValuationAssumptions(BaseModel):
    discount_rate: float
    terminal_growth_rate: float
    projection_years: int
    margin_of_safety: float
    growth_rate: float


class ValuationRatios(BaseModel):
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    enterprise_to_ebitda: float | None = None
    dividend_yield: float | None = None
    market_cap_to_free_cash_flow: float | None = None


class ValuationResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    symbol: str
    currency: str | None = None
    current_price: float | None = None
    intrinsic_value_per_share: float | None = None
    margin_of_safety_price: float | None = None
    upside_downside_percent: float | None = None
    enterprise_value: float | None = None
    equity_value: float | None = None
    assumptions: ValuationAssumptions
    ratios: ValuationRatios
    warnings: list[str] = Field(default_factory=list)


class StockSnapshot(BaseModel):
    symbol: str
    quote: QuoteResponse
    financials: FinancialMetrics
    warnings: list[str] = Field(default_factory=list)
