import math
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


APPROVED_BANK_METRIC_KEYS = frozenset(
    {
        "cet1_ratio",
        "npl_ratio",
        "loan_loss_coverage",
        "regulatory_capital_headroom",
    }
)


class FactProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    concept: str | None = None
    form: str | None = None
    accession: str | None = None
    period_end: date | None = None
    filed_at: date | None = None
    unit: str | None = None


class FinancialPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_end: date
    fiscal_year: int | None = None
    is_ttm: bool = False
    currency: str
    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    stock_based_compensation: float | None = None
    interest_paid_outside_operating: float | None = None
    revenue: float | None = None
    net_income_common: float | None = None
    common_equity: float | None = None
    cash_and_equivalents: float | None = None
    total_assets: float | None = None
    total_debt: float | None = None
    diluted_shares: float | None = None
    common_dividends: float | None = None
    distribution_per_unit: float | None = None
    nav_per_unit: float | None = None
    sources: dict[str, FactProvenance] = Field(default_factory=dict)


class ValuationFundamentals(BaseModel):
    symbol: str
    exchange: str
    currency: str
    primary_source: str
    provider_security_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    issuer_classification: str | None = None
    current_diluted_shares: float | None = None
    bank_metrics: dict[str, float] = Field(default_factory=dict)
    periods: list[FinancialPeriod] = Field(default_factory=list)
    fetched_at: datetime
    sources: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("bank_metrics")
    @classmethod
    def validate_bank_metrics(cls, metrics: dict[str, float]) -> dict[str, float]:
        unsupported = sorted(set(metrics) - APPROVED_BANK_METRIC_KEYS)
        if unsupported:
            raise ValueError(
                f"unsupported bank metric keys: {', '.join(unsupported)}"
            )
        if any(not math.isfinite(value) for value in metrics.values()):
            raise ValueError("bank metrics must be finite")
        return metrics


class ScenarioAssumptions(BaseModel):
    name: Literal["bear", "base", "bull"]
    starting_factor: float
    initial_growth: float
    required_return: float
    terminal_growth: float
    projection_years: int = 10


class ModelResult(BaseModel):
    method: str
    detected_company_type: str
    bear: float
    base: float
    bull: float
    details: dict[str, object]
    assumptions: dict[str, object]
    quality: dict[str, object]
    warnings: list[str] = Field(default_factory=list)
