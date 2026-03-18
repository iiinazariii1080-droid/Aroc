"""Per-service environment settings for mqtt-telemetry."""

import logging
from dataclasses import dataclass
from typing import Any

from shared.broker_fetch import build_mqtt_connection_config
from shared.broker_fetch import fetch_config as _fetch_mqtt
from shared.config_types import MQTTConnectionConfig
from shared.constants import (
    DEFAULT_ROBOT_ID,
    HOST_LAN_IP_DEFAULT,
)
from shared.env import env, env_bool, env_float, env_int, validate_robot_id
from shared.utils import singleton_factory

logger = logging.getLogger(__name__)

# Default Janus WebSocket ports
_JANUS_DEPTH_PORT = 8188
_JANUS_COLOR_PORT = 8189


@dataclass(frozen=True)
class TelemetryServiceSettings:
    config_api_url: str = "http://config-api:8100"

    robot_id: str = DEFAULT_ROBOT_ID
    mqtt_client_id: str = ""

    local_ip: str = HOST_LAN_IP_DEFAULT
    is_remote: bool = False
    remote_address: str = ""

    poll_interval_seconds: int = 5
    telemetry_http_timeout: int = 3

    websocket_check_timeout: float = 2.0
    websocket_check_interval: float = 30.0
    websocket_tls_insecure: bool = False

    janus_ws_depth: str = ""
    janus_ws_color: str = ""

    mqtt_publish_qos: int = 1
    log_level: str = "INFO"
    internal_service_key: str = ""
    allow_tls_insecure_remote: bool = False

    def __post_init__(self) -> None:
        """Validate semantic constraints on settings values."""
        errors: list[str] = []
        if self.poll_interval_seconds <= 0:
            errors.append(f"poll_interval_seconds must be > 0, got {self.poll_interval_seconds}")
        if self.telemetry_http_timeout <= 0:
            errors.append(f"telemetry_http_timeout must be > 0, got {self.telemetry_http_timeout}")
        if self.websocket_check_timeout <= 0:
            errors.append(f"websocket_check_timeout must be > 0, got {self.websocket_check_timeout}")
        if self.websocket_check_interval <= 0:
            errors.append(f"websocket_check_interval must be > 0, got {self.websocket_check_interval}")
        if not 0 <= self.mqtt_publish_qos <= 2:
            errors.append(f"mqtt_publish_qos must be 0, 1, or 2, got {self.mqtt_publish_qos}")
        if self.is_remote and self.websocket_tls_insecure and not self.allow_tls_insecure_remote:
            errors.append(
                "WEBSOCKET_TLS_INSECURE=true in remote mode is blocked by default "
                "(MITM risk). Set ALLOW_TLS_INSECURE_REMOTE=true to override"
            )
        if errors:
            raise ValueError("Invalid TelemetryServiceSettings:\n  " + "\n  ".join(errors))

    @classmethod
    def from_env(cls) -> "TelemetryServiceSettings":
        return cls(
            config_api_url=env("CONFIG_API_URL", "http://config-api:8100"),
            robot_id=validate_robot_id(env("ROBOT_ID", DEFAULT_ROBOT_ID)),
            mqtt_client_id=env("MQTT_CLIENT_ID", ""),
            local_ip=env("LOCAL_IP", HOST_LAN_IP_DEFAULT),
            is_remote=env_bool("IS_REMOTE", False),
            remote_address=env("REMOTE_ADDRESS", ""),
            poll_interval_seconds=env_int("POLL_INTERVAL_SECONDS", 5),
            telemetry_http_timeout=env_int("TELEMETRY_HTTP_TIMEOUT", 3),
            websocket_check_timeout=env_float("WEBSOCKET_CHECK_TIMEOUT", 2.0),
            websocket_check_interval=env_float("WEBSOCKET_CHECK_INTERVAL", 30.0),
            websocket_tls_insecure=env_bool("WEBSOCKET_TLS_INSECURE", False),
            janus_ws_depth=env("JANUS_WS_DEPTH", ""),
            janus_ws_color=env("JANUS_WS_COLOR", ""),
            mqtt_publish_qos=env_int("MQTT_PUBLISH_QOS", 1),
            log_level=env("LOG_LEVEL", "INFO"),
            internal_service_key=env("INTERNAL_SERVICE_KEY", ""),
            allow_tls_insecure_remote=env_bool("ALLOW_TLS_INSECURE_REMOTE", False),
        )

    def _effective_janus_url(self, override: str, port: int) -> str:
        if override:
            return override
        scheme = "wss" if self.is_remote else "ws"
        host = self.remote_address if self.is_remote else self.local_ip
        return f"{scheme}://{host}:{port}" if host else ""

    @property
    def effective_janus_ws_depth(self) -> str:
        return self._effective_janus_url(self.janus_ws_depth, _JANUS_DEPTH_PORT)

    @property
    def effective_janus_ws_color(self) -> str:
        return self._effective_janus_url(self.janus_ws_color, _JANUS_COLOR_PORT)


_get_settings = singleton_factory(TelemetryServiceSettings.from_env)


def get_settings() -> TelemetryServiceSettings:
    return _get_settings()


def fetch_mqtt_connection_config(settings: TelemetryServiceSettings) -> MQTTConnectionConfig:
    """Fetch MQTT connection config from config-api with retry/backoff."""
    robot_id = settings.robot_id
    client_id = settings.mqtt_client_id or f"{robot_id}-telemetry"

    def _build(data: dict[str, Any]) -> MQTTConnectionConfig:
        return build_mqtt_connection_config(
            data,
            robot_id,
            client_id,
            settings.mqtt_publish_qos,
        )

    return _fetch_mqtt(settings.config_api_url, _build, api_key=settings.internal_service_key or None)
