from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    mcp_transport: Literal["stdio", "streamable-http"] = "streamable-http"
    mcp_path: str = "/mcp"

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0, le=10)
    http_backoff_seconds: float = Field(default=0.5, ge=0)
    user_agent: str = "turkiye-energy-mcp/0.1 (+https://github.com/emresoykan/turkiye-energy-mcp)"

    cache_daily_ttl_seconds: int = Field(default=600, ge=0)
    cache_monthly_ttl_seconds: int = Field(default=21600, ge=0)
    cache_historical_ttl_seconds: int = Field(default=86400, ge=0)
    cache_plants_ttl_seconds: int = Field(default=86400, ge=0)

    teias_base_url: str = "https://www.teias.gov.tr"
    teias_file_base_url: str = "https://webim.teias.gov.tr"
    teias_annual_report_year: int = Field(default=2024, ge=2008, le=2100)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
