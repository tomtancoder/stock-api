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
