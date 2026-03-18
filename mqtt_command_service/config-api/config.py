"""Per-service environment settings for config-api."""

from dataclasses import dataclass
from pathlib import Path

from shared.env import env, env_bool, env_int
from shared.utils import singleton_factory


@dataclass(frozen=True)
class ConfigAPISettings:
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    config_file_path: str = "/data/config.json"
    cert_dir: str = "/certs"
    log_level: str = "INFO"
    cors_origins_raw: str = ""

    # Security
    auth_disabled: bool = False
    emergency_api_key: str = ""
    hmac_secret: str = ""
    allow_unauthenticated_read: bool = False
    internal_service_key: str = ""

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not 1 <= self.api_port <= 65535:
            errors.append(f"api_port must be 1-65535, got {self.api_port}")
        if errors:
            raise ValueError("Invalid ConfigAPISettings:\n  " + "\n  ".join(errors))

    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS_ORIGINS env var into a list of origins."""
        if not self.cors_origins_raw:
            return []
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @classmethod
    def from_env(cls) -> "ConfigAPISettings":
        return cls(
            api_host=env("API_HOST", "0.0.0.0"),
            api_port=env_int("API_PORT", 8100),
            config_file_path=env("CONFIG_FILE_PATH", "/data/config.json"),
            cert_dir=env("CERT_DIR", "/certs"),
            log_level=env("LOG_LEVEL", "INFO"),
            cors_origins_raw=env("CORS_ORIGINS", ""),
            auth_disabled=env_bool("AUTH_DISABLED", False),
            emergency_api_key=env("EMERGENCY_API_KEY", ""),
            hmac_secret=env("HMAC_SECRET", ""),
            allow_unauthenticated_read=env_bool("ALLOW_UNAUTHENTICATED_READ", False),
            internal_service_key=env("INTERNAL_SERVICE_KEY", ""),
        )

    @property
    def cert_ca_file(self) -> Path:
        from shared.constants import get_cert_path

        return get_cert_path("ca.crt")

    @property
    def cert_client_cert_file(self) -> Path:
        from shared.constants import get_cert_path

        return get_cert_path("client.crt")

    @property
    def cert_client_key_file(self) -> Path:
        from shared.constants import get_cert_path

        return get_cert_path("client.key")


_get_settings = singleton_factory(ConfigAPISettings.from_env)


def get_settings() -> ConfigAPISettings:
    return _get_settings()
