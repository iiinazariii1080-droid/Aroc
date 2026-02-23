"""Application configuration and settings."""
from pathlib import Path

from constants import DEFAULT_CERT_STORAGE_DIR
from env_settings import get_env_settings


class Settings:
    """Application settings — reads values from the centralized EnvSettings."""

    # API Settings
    API_TITLE: str = "MQTT Bridge Configuration API"
    API_DESCRIPTION: str = "API for managing MQTT broker settings and credentials"
    API_VERSION: str = "1.0.0"

    def __init__(self) -> None:
        env = get_env_settings()
        self.API_HOST: str = env.api_host
        self.API_PORT: int = env.api_port
        self.CERT_STORAGE_DIR: Path = DEFAULT_CERT_STORAGE_DIR
        self.RATE_LIMIT_ENABLED: bool = env.rate_limit_enabled
        self.LOG_LEVEL: str = env.log_level
        self.JSON_LOGS: bool = env.json_logs
        self.LOG_FILE: str | None = env.log_file

    def ensure_directories(self) -> None:
        """Ensure required directories exist."""
        self.CERT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()

