from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCK_API_", env_file=".env")

    cache_ttl_seconds: int = Field(default=3600, ge=1)
    default_exchange: str = Field(default="NASDAQ", min_length=1)
    default_timeframe: str = Field(default="1D", min_length=1)
    valuation_cache_ttl_seconds: int = Field(default=86_400, ge=60)
    valuation_quote_ttl_seconds: int = Field(default=300, ge=1)
    valuation_stale_ttl_seconds: int = Field(default=604_800, ge=300)
    sec_user_agent: str | None = Field(default=None, min_length=3)
    screener_cache_ttl_seconds: int = Field(default=3600, ge=1)
    screener_batch_size: int = Field(default=75, ge=1, le=250)
    maximum_market_data_age_days: int = Field(default=5, ge=1, le=30)


@lru_cache
def get_settings() -> Settings:
    return Settings()
