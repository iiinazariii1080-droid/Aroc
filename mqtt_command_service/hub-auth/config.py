"""Per-service environment settings for hub-auth."""

import os
from dataclasses import dataclass

from shared.env import env, env_float, env_int
from shared.utils import singleton_factory


@dataclass(frozen=True)
class HubAuthServiceSettings:
    api_host: str = "0.0.0.0"
    api_port: int = 8101
    hub_base_url: str = ""
    hub_robot_id: str | None = None
    hub_api_key: str | None = None
    hub_auth_refresh_margin: float = 60.0
    hub_auth_timeout: float = 5.0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not 1 <= self.api_port <= 65535:
            errors.append(f"api_port must be 1-65535, got {self.api_port}")
        if self.hub_auth_refresh_margin <= 0:
            errors.append(f"hub_auth_refresh_margin must be > 0, got {self.hub_auth_refresh_margin}")
        if self.hub_auth_timeout <= 0:
            errors.append(f"hub_auth_timeout must be > 0, got {self.hub_auth_timeout}")
        if errors:
            raise ValueError("Invalid HubAuthServiceSettings:\n  " + "\n  ".join(errors))

    @classmethod
    def from_env(cls) -> "HubAuthServiceSettings":
        return cls(
            api_host=env("API_HOST", "0.0.0.0"),
            api_port=env_int("API_PORT", 8101),
            hub_base_url=env("HUB_BASE_URL", ""),
            hub_robot_id=os.environ.get("HUB_ROBOT_ID"),
            hub_api_key=os.environ.get("HUB_API_KEY"),
            hub_auth_refresh_margin=env_float("HUB_AUTH_REFRESH_MARGIN", 60.0),
            hub_auth_timeout=env_float("HUB_AUTH_TIMEOUT", 5.0),
            log_level=env("LOG_LEVEL", "INFO"),
        )


_get_settings = singleton_factory(HubAuthServiceSettings.from_env)


def get_settings() -> HubAuthServiceSettings:
    return _get_settings()
