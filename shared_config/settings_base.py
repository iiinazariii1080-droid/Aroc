"""Base settings class for all services.

Every service settings class should inherit from ``BaseServiceSettings``
to get consistent env-file loading, common fields, and typing.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Common fields shared across all micro-services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = Field(default="INFO", description="Logging level")
    service_host: str = Field(default="0.0.0.0", description="Bind address")
    service_port: int = Field(default=8000, description="Bind port")
    app_env: str = Field(default="dev", description="Environment: dev | staging | prod")
