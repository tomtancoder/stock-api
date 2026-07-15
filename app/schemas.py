from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, model_validator


class QuoteResponse(BaseModel):
    symbol: str
    exchange: str
    name: str | None = None
    price: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    currency: str | None = None
    market_state: str | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    source: str | None = None
    timestamp: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BreakoutSetupState(str, Enum):
    PRE_BREAKOUT = "Pre-Breakout"
    FRESH_BREAKOUT = "Fresh Breakout"
    CONFIRMED_BREAKOUT = "Confirmed Breakout"
    BREAKOUT_RETEST = "Breakout Retest"
    TREND_TRANSITION = "Trend Transition"
    FAILED_BREAKOUT = "Failed Breakout"
    NO_VALID_SETUP = "No Valid Setup"


class BreakoutRating(str, Enum):
    STRONG_SETUP = "Strong Setup"
    STARTER_SETUP = "Starter Setup"
    WATCHLIST = "Watchlist"
    AVOID = "Avoid"


class DataStatus(str, Enum):
    READY = "ready"
    INSUFFICIENT_HISTORY = "insufficient_history"
    STALE = "stale"
    PARTIAL = "partial"
    ERROR = "error"


class FourHourStatus(str, Enum):
    CONFIRMED = "4H Confirmed"
    RETEST_HELD = "4H Retest Held"
    WAIT = "Wait for 4H"
    WEAK = "4H Weak"
    UNAVAILABLE = "Unavailable"


class BreakoutComponentScore(BaseModel):
    score: int = Field(ge=0)
    max_score: int = Field(ge=1)
    flags: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_does_not_exceed_component_maximum(self) -> Self:
        if self.score > self.max_score:
            raise ValueError("score must not exceed max_score")
        return self


class BreakoutLevelInfo(BaseModel):
    window: int | None = None
    price: float | None = None
    buffer: float | None = None
    breakout_bars_ago: int | None = None
    breakout_percent: float | None = None
    close_location: float | None = None
    base_depth_pct: float | None = None


class BreakoutIndicatorSnapshot(BaseModel):
    close: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    ema200_prior: float | None = None
    rsi14: float | None = None
    atr14: float | None = None
    adx14: float | None = None
    plus_di14: float | None = None
    minus_di14: float | None = None
    cmf20: float | None = None
    volume_ratio: float | None = None
    stock_return_63: float | None = None
    benchmark_return_63: float | None = None


class BreakoutRiskSnapshot(BaseModel):
    invalidation_price: float | None = None
    extension_atr: float | None = None
    initial_risk_pct: float | None = None


class BreakoutAnalysisResponse(BaseModel):
    symbol: str
    exchange: str
    benchmark_symbol: str
    as_of: str | None = None
    data_status: DataStatus
    rating: BreakoutRating | None = None
    setup_state: BreakoutSetupState
    total_score: int | None = Field(default=None, ge=0, le=18)
    four_hour_status: FourHourStatus = FourHourStatus.UNAVAILABLE
    breakout: BreakoutComponentScore | None = None
    trend: BreakoutComponentScore | None = None
    volume: BreakoutComponentScore | None = None
    momentum: BreakoutComponentScore | None = None
    relative_strength: BreakoutComponentScore | None = None
    entry_quality: BreakoutComponentScore | None = None
    level: BreakoutLevelInfo | None = None
    indicators: BreakoutIndicatorSnapshot | None = None
    risk: BreakoutRiskSnapshot | None = None
    flags: list[str] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BreakoutScreenerError(BaseModel):
    symbol: str
    error_type: str
    message: str


class BreakoutScreenerResponse(BaseModel):
    as_of: str | None = None
    market: str
    benchmark_symbols: list[str] = Field(default_factory=list)
    scanned_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    excluded_low_liquidity_count: int = Field(default=0, ge=0)
    returned_count: int = Field(ge=0)
    results: list[BreakoutAnalysisResponse] = Field(default_factory=list)
    errors: list[BreakoutScreenerError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BacktestRequest(BaseModel):
    strategy: str = Field(default="rsi", min_length=1)
    period: str = Field(default="1y", min_length=1, max_length=16)
    initial_capital: float = Field(default=10_000.0, gt=0)
    commission_pct: float = Field(default=0.1, ge=0)
    slippage_pct: float = Field(default=0.05, ge=0)
    interval: str = Field(default="1d", min_length=1, max_length=16)
    include_trade_log: bool = False
    include_equity_curve: bool = False


class CompareStrategiesRequest(BaseModel):
    period: str = Field(default="1y", min_length=1, max_length=16)
    initial_capital: float = Field(default=10_000.0, gt=0)
    commission_pct: float = Field(default=0.1, ge=0)
    slippage_pct: float = Field(default=0.05, ge=0)
    interval: str = Field(default="1d", min_length=1, max_length=16)


class WalkForwardBacktestRequest(BaseModel):
    strategy: str = Field(default="rsi", min_length=1)
    period: str = Field(default="2y", min_length=1, max_length=16)
    initial_capital: float = Field(default=10_000.0, gt=0)
    commission_pct: float = Field(default=0.1, ge=0)
    slippage_pct: float = Field(default=0.05, ge=0)
    n_splits: int = Field(default=3, ge=2, le=10)
    train_ratio: float = Field(default=0.7, gt=0, lt=1)
    interval: str = Field(default="1d", min_length=1, max_length=16)


class IntrinsicValueRange(BaseModel):
    bear: float = Field(gt=0, allow_inf_nan=False)
    base: float = Field(gt=0, allow_inf_nan=False)
    bull: float = Field(gt=0, allow_inf_nan=False)
    margin_of_safety_price: float
    price_to_base_value: float
    upside_downside_percent: float

    @model_validator(mode="after")
    def validate_scenario_order(self) -> Self:
        if not self.bear <= self.base <= self.bull:
            raise ValueError("intrinsic values must satisfy bear <= base <= bull")
        return self


class ValuationDataQuality(BaseModel):
    primary_source: str | None = None
    financials_as_of: date | None = None
    valuation_as_of: datetime
    next_refresh_at: datetime | None = None
    stale: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class ValuationQuality(BaseModel):
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class OwnerEarningsHistoryEntry(BaseModel):
    period_end: date
    currency: str
    operating_cash_flow: float = Field(allow_inf_nan=False)
    maintenance_capex: float = Field(ge=0, allow_inf_nan=False)
    maintenance_capex_method: str
    stock_based_compensation: float = Field(ge=0, allow_inf_nan=False)
    interest_paid_outside_operating: float = Field(ge=0, allow_inf_nan=False)
    owner_earnings: float = Field(allow_inf_nan=False)


class OwnerEarningsValuationDetails(BaseModel):
    method: Literal["owner_earnings_dcf"]
    normalized_owner_earnings: float
    owner_earnings_per_share: float
    maintenance_capex_method: str
    annual_history: list[OwnerEarningsHistoryEntry] = Field(default_factory=list)
    derived_growth: float
    usable_years: int


class BankValuationDetails(BaseModel):
    method: Literal["bank_residual_income"]
    normalized_roe: float
    book_value_per_share: float
    payout_ratio: float
    usable_years: int
    projected_book_equity: dict[str, list[float]]
    cet1_ratio: float | None = None
    npl_ratio: float | None = None
    loan_loss_coverage: float | None = None


class ReitValuationDetails(BaseModel):
    method: Literal["reit_distribution_nav", "reit_distribution_only"]
    normalized_dpu: float
    nav_per_unit: float | None
    price_to_nav: float | None
    distribution_yield: float
    usable_years: int
    present_value_distributions: dict[str, float]
    present_value_terminal: dict[str, float]
    aggregate_leverage: float | None = None
    interest_coverage: float | None = None
    occupancy: float | None = None
    wale_years: float | None = None


ValuationModelDetails = Annotated[
    OwnerEarningsValuationDetails | BankValuationDetails | ReitValuationDetails,
    Field(discriminator="method"),
]


class ValuationResponse(BaseModel):
    symbol: str
    exchange: str
    currency: str
    detected_company_type: str
    method: str | None = None
    classification_sources: list[str] = Field(default_factory=list)
    status: Literal[
        "cheap",
        "fair",
        "expensive",
        "very_expensive",
        "valuation_unreliable",
    ]
    confidence: Literal["high", "medium", "low"] | None = None
    current_price: float = Field(gt=0, allow_inf_nan=False)
    price_as_of: datetime
    intrinsic_value: IntrinsicValueRange | None = None
    model_details: ValuationModelDetails | None = None
    quality: ValuationQuality
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_quality: ValuationDataQuality
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_claims(self) -> Self:
        claims = (
            self.method,
            self.confidence,
            self.intrinsic_value,
            self.model_details,
        )
        if self.status == "valuation_unreliable":
            if any(claim is not None for claim in claims):
                raise ValueError(
                    "valuation_unreliable responses must omit valuation claims"
                )
        elif any(claim is None for claim in claims):
            raise ValueError("reliable valuation responses require valuation claims")
        return self
