from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCK_API_", env_file=".env")

    cache_ttl_seconds: int = Field(default=900, ge=1)
    default_discount_rate: float = Field(default=0.10, gt=0, lt=1)
    default_terminal_growth_rate: float = Field(default=0.025, ge=0, lt=0.10)
    default_projection_years: int = Field(default=5, ge=1, le=10)
    default_margin_of_safety: float = Field(default=0.25, ge=0, lt=1)
    default_growth_rate: float = Field(default=0.06, ge=-0.50, lt=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
