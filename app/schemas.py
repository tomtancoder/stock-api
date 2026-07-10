from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class QuoteResponse(BaseModel):
    symbol: str
    exchange: str
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
    bear: float
    base: float
    bull: float
    margin_of_safety_price: float
    price_to_base_value: float
    upside_downside_percent: float


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


class OwnerEarningsValuationDetails(BaseModel):
    method: Literal["owner_earnings_dcf"]
    normalized_owner_earnings: float
    owner_earnings_per_share: float
    maintenance_capex_method: str
    annual_history: list[dict[str, Any]] = Field(default_factory=list)
    derived_growth: float
    usable_years: int


class ValuationResponse(BaseModel):
    symbol: str
    exchange: str
    currency: str | None = None
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
    current_price: float | None = None
    price_as_of: datetime | None = None
    intrinsic_value: IntrinsicValueRange | None = None
    model_details: OwnerEarningsValuationDetails | None = None
    quality: ValuationQuality
    assumptions: dict[str, Any] = Field(default_factory=dict)
    data_quality: ValuationDataQuality
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
