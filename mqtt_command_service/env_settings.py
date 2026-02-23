"""Centralized, typed environment settings.

All *static* environment variables (those **not** stored in the config
database at runtime) are defined here as typed fields with their
defaults.  ``EnvSettings.from_env()`` reads ``os.environ`` once and
validates types so that misconfiguration surfaces immediately at startup.

Dynamic MQTT settings (broker, port, credentials, TLS flags) are loaded
via ``_get_config_value`` / ``_get_config_value_bool`` in ``config.py``
because they can be changed at runtime through the API / MQTT and are
persisted to the config database.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {key}={raw!r} is not a valid float"
        )


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {key}={raw!r} is not a valid integer"
        )


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes", "on")


@dataclass(frozen=True)
class EnvSettings:
    """Immutable snapshot of every *static* environment variable.

    Create via ``EnvSettings.from_env()`` at startup.

    Fields are grouped by subsystem for readability:
    - MQTT client ID (non-dynamic)
    - HTTP / task polling
    - Network / IPs
    - Service URLs
    - HUB auth
    - Status / heartbeat
    - API server
    - Logging
    - Feature flags
    """

    # ---- MQTT client (non-dynamic) ---------------------------------
    mqtt_client_id: str | None = None  # None → derived from robot_id

    # ---- HTTP / task polling ----------------------------------------
    http_timeout: float = 5.0
    task_poll_interval: float = 1.0
    task_poll_timeout: float = 120.0

    # ---- Network ----------------------------------------------------
    local_ip: str = "192.168.1.10"
    depth_camera_ip: str | None = None  # None → same as local_ip

    # ---- Service URLs / map -----------------------------------------
    api_port: int = 7900
    service_use_local: bool = False
    robot_service_url: str = "http://UNCONFIGURED.invalid/api/v1/robot"
    igus_service_url: str = "http://UNCONFIGURED.invalid/api/v1/igus"
    xarm_service_url: str = "http://UNCONFIGURED.invalid/api/v1/xarm"
    symovo_service_url: str = "http://UNCONFIGURED.invalid/api/v1/symovo"
    service_map_json: str | None = None  # raw JSON override

    # ---- HUB auth ---------------------------------------------------
    hub_base_url: str | None = None
    hub_robot_id: str | None = None
    hub_api_key: str | None = None
    hub_auth_refresh_margin: float = 60.0

    # ---- Status / heartbeat -----------------------------------------
    status_heartbeat_interval: float = 15.0
    mqtt_publish_qos: int = 1

    # ---- API server -------------------------------------------------
    api_host: str = "0.0.0.0"
    api_enabled: bool = True

    # ---- Logging ----------------------------------------------------
    log_level: str = "INFO"
    json_logs: bool = False
    log_file: str | None = None

    # ---- Security ---------------------------------------------------
    auth_disabled: bool = False
    disable_auth_for_config: bool = False
    emergency_api_key: str = "tR-UZ2j2KutE6OYlEGbsx0h5qe071L-gC5kd1hHKfw4"  # Override via EMERGENCY_API_KEY env var
    hmac_secret: str = ""
    rate_limit_enabled: bool = False

    # ---- Telemetry --------------------------------------------------
    is_remote: bool = True
    remote_address: str = "api.techvisioncloud.pl/api/v1"
    robot_id: str = "ROBOT_01"
    poll_interval_seconds: int = 5
    telemetry_http_timeout: int = 2  # telemetry-specific HTTP timeout
    websocket_check_timeout: float = 2.0
    websocket_check_interval: float = 10.0
    websocket_tls_insecure: bool = False
    janus_ws_depth: str | None = None  # None → derived from remote/local
    janus_ws_color: str | None = None  # None → derived from remote/local

    # ---- Misc -------------------------------------------------------
    config_db_path: str | None = None
    long_operations_timeouts: str | None = None  # raw JSON

    @classmethod
    def from_env(cls) -> EnvSettings:
        """Read all static env vars in one pass and validate types.

        Raises ``ValueError`` on malformed numeric values so that
        misconfigurations surface at startup rather than at first use.
        """
        local_ip = _env("LOCAL_IP", "192.168.1.10")
        depth_camera_raw = os.environ.get("DEPTH_CAMERA_IP")

        return cls(
            # MQTT client
            mqtt_client_id=os.environ.get("MQTT_CLIENT_ID"),
            # HTTP / polling
            http_timeout=_env_float("HTTP_TIMEOUT", 5.0),
            task_poll_interval=_env_float("TASK_POLL_INTERVAL", 1.0),
            task_poll_timeout=_env_float("TASK_POLL_TIMEOUT", 120.0),
            # Network
            local_ip=local_ip,
            depth_camera_ip=depth_camera_raw if depth_camera_raw else None,
            # Service URLs
            api_port=_env_int("API_PORT", 7900),
            service_use_local=_env_bool("SERVICE_USE_LOCAL", False),
            robot_service_url=_env("ROBOT_SERVICE_URL", "http://UNCONFIGURED.invalid/api/v1/robot"),
            igus_service_url=_env("IGUS_SERVICE_URL", "http://UNCONFIGURED.invalid/api/v1/igus"),
            xarm_service_url=_env("XARM_SERVICE_URL", "http://UNCONFIGURED.invalid/api/v1/xarm"),
            symovo_service_url=_env("SYMOVO_SERVICE_URL", "http://UNCONFIGURED.invalid/api/v1/symovo"),
            service_map_json=os.environ.get("SERVICE_MAP_JSON"),
            # HUB
            hub_base_url=os.environ.get("HUB_BASE_URL"),
            hub_robot_id=os.environ.get("HUB_ROBOT_ID"),
            hub_api_key=os.environ.get("HUB_API_KEY"),
            hub_auth_refresh_margin=_env_float("HUB_AUTH_REFRESH_MARGIN", 60.0),
            # Status
            status_heartbeat_interval=_env_float("STATUS_HEARTBEAT_INTERVAL", 15.0),
            mqtt_publish_qos=_env_int("MQTT_PUBLISH_QOS", 1),
            # API
            api_host=_env("API_HOST", "0.0.0.0"),
            api_enabled=_env_bool("API_ENABLED", True),
            # Logging
            log_level=_env("LOG_LEVEL", "INFO"),
            json_logs=_env_bool("JSON_LOGS", False),
            log_file=os.environ.get("LOG_FILE"),
            # Security
            auth_disabled=_env_bool("AUTH_DISABLED", False),
            disable_auth_for_config=_env_bool("DISABLE_AUTH_FOR_CONFIG", False),
            emergency_api_key=_env("EMERGENCY_API_KEY", "tR-UZ2j2KutE6OYlEGbsx0h5qe071L-gC5kd1hHKfw4"),
            hmac_secret=_env("HMAC_SECRET", ""),
            rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", False),
            # Telemetry
            is_remote=_env_bool("IS_REMOTE", True),
            remote_address=_env("REMOTE_ADDRESS", "api.techvisioncloud.pl/api/v1"),
            robot_id=_env("ROBOT_ID", "ROBOT_01"),
            poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 5),
            telemetry_http_timeout=_env_int("HTTP_TIMEOUT_SECONDS", 2),
            websocket_check_timeout=_env_float("WEBSOCKET_CHECK_TIMEOUT", 2.0),
            websocket_check_interval=_env_float("WEBSOCKET_CHECK_INTERVAL", 10.0),
            websocket_tls_insecure=_env_bool("WEBSOCKET_TLS_INSECURE", False),
            janus_ws_depth=os.environ.get("JANUS_WS_DEPTH"),
            janus_ws_color=os.environ.get("JANUS_WS_COLOR"),
            # Misc
            config_db_path=os.environ.get("CONFIG_DB_PATH"),
            long_operations_timeouts=os.environ.get("LONG_OPERATIONS_TIMEOUTS"),
        )

    @property
    def effective_depth_camera_ip(self) -> str:
        return self.depth_camera_ip or self.local_ip

    @property
    def effective_janus_ws_depth(self) -> str:
        """Resolve JANUS_WS_DEPTH: explicit env → derived from remote/local."""
        if self.janus_ws_depth:
            return self.janus_ws_depth
        if self.is_remote:
            return f"wss://{self.remote_address}/depth_camera/janus-ws"
        return f"wss://{self.effective_depth_camera_ip}/janus-ws"

    @property
    def effective_janus_ws_color(self) -> str:
        """Resolve JANUS_WS_COLOR: explicit env → derived from remote/local."""
        if self.janus_ws_color:
            return self.janus_ws_color
        if self.is_remote:
            return f"wss://{self.remote_address}/color_camera/janus-ws"
        return f"wss://{self.local_ip}/janus-ws"

    def parse_long_operations(self) -> dict[str, dict[str, float]] | None:
        """Parse LONG_OPERATIONS_TIMEOUTS JSON, returning None on failure."""
        if not self.long_operations_timeouts:
            return None
        try:
            result: dict[str, dict[str, float]] = json.loads(self.long_operations_timeouts)
            return result
        except Exception:
            logger.warning("Failed to parse LONG_OPERATIONS_TIMEOUTS, using defaults")
            return None


# Module-level singleton, created once at import time.
# Safe because os.environ is read-only in normal operation.
_settings: EnvSettings | None = None


def get_env_settings() -> EnvSettings:
    """Return the cached EnvSettings singleton (created on first call)."""
    global _settings
    if _settings is None:
        _settings = EnvSettings.from_env()
    return _settings


def reset_env_settings() -> None:
    """Force re-read from os.environ.  Used in tests only."""
    global _settings
    _settings = None
