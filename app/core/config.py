from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCK_API_", env_file=".env")

    cache_ttl_seconds: int = Field(default=3600, ge=1)
    default_exchange: str = Field(default="NASDAQ", min_length=1)
    default_timeframe: str = Field(default="1D", min_length=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
