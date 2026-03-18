"""Per-service environment settings for mqtt-bridge."""

import json
import logging
import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from shared.broker_fetch import build_mqtt_connection_config
from shared.config_types import BridgeConfig, ServiceConfig
from shared.constants import (
    DEFAULT_ROBOT_ID,
    HOST_LAN_IP_DEFAULT,
    SAFETY_GATE_STARTUP_GRACE_SECONDS,
    SERVICE_PORT_IGUS,
    SERVICE_PORT_ROBOT,
    SERVICE_PORT_SYMOVO,
    SERVICE_PORT_XARM,
)
from shared.env import env, env_bool, env_float, env_int, validate_robot_id
from shared.utils import singleton_factory

logger = logging.getLogger(__name__)

DEFAULT_LONG_OPERATIONS: dict[str, dict[str, float]] = {
    "igus": {"/move": 30.0, "/reference": 30.0, "/fault_reset": 30.0},
}

# Per-service path prefix allowlists.
# Only requests whose normalized path starts with one of these prefixes are allowed.
# Empty tuple = unrestricted (for dynamic/user-defined services via SERVICE_MAP_JSON).
DEFAULT_ALLOWED_PATH_PREFIXES: dict[str, tuple[str, ...]] = {
    "robot": ("/tasks/", "/status/", "/health"),
    "igus": ("/move", "/reference", "/fault_reset", "/status", "/health"),
    "xarm": ("/move", "/status", "/health", "/tasks/"),
    "symovo": ("/move", "/status", "/health", "/tasks/"),
}

# Restrictive default for dynamically added services (via SERVICE_MAP_JSON).
# Only status and health endpoints are allowed unless explicitly configured.
DYNAMIC_SERVICE_DEFAULT_PREFIXES: tuple[str, ...] = ("/status", "/health")


@dataclass(frozen=True)
class BridgeServiceSettings:
    config_api_url: str = "http://config-api:8100"
    hub_auth_url: str = ""
    hub_auth_timeout: float = 5.0

    robot_id: str = DEFAULT_ROBOT_ID
    mqtt_client_id: str | None = None

    http_timeout: float = 5.0
    task_poll_interval: float = 1.0
    task_poll_timeout: float = 120.0

    local_ip: str = HOST_LAN_IP_DEFAULT
    service_use_local: bool = False
    robot_service_url: str | None = None
    igus_service_url: str | None = None
    xarm_service_url: str | None = None
    symovo_service_url: str | None = None
    service_map_json: str | None = None

    internal_service_key: str = ""

    status_heartbeat_interval: float = 15.0
    mqtt_publish_qos: int = 1
    safety_gate_startup_grace: float = SAFETY_GATE_STARTUP_GRACE_SECONDS

    log_level: str = "INFO"
    long_operations_timeouts: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.http_timeout <= 0:
            errors.append(f"http_timeout must be > 0, got {self.http_timeout}")
        if self.task_poll_interval <= 0:
            errors.append(f"task_poll_interval must be > 0, got {self.task_poll_interval}")
        if self.task_poll_timeout <= 0:
            errors.append(f"task_poll_timeout must be > 0, got {self.task_poll_timeout}")
        if self.status_heartbeat_interval <= 0:
            errors.append(f"status_heartbeat_interval must be > 0, got {self.status_heartbeat_interval}")
        if not 0 <= self.mqtt_publish_qos <= 2:
            errors.append(f"mqtt_publish_qos must be 0, 1, or 2, got {self.mqtt_publish_qos}")
        if errors:
            raise ValueError("Invalid BridgeServiceSettings:\n  " + "\n  ".join(errors))

    def __repr__(self) -> str:
        masked_key = "***" if self.internal_service_key else ""
        fields = []
        for f in self.__dataclass_fields__:
            val = masked_key if f == "internal_service_key" else getattr(self, f)
            fields.append(f"{f}={val!r}")
        return f"{self.__class__.__name__}({', '.join(fields)})"

    @classmethod
    def from_env(cls) -> "BridgeServiceSettings":
        return cls(
            config_api_url=env("CONFIG_API_URL", "http://config-api:8100"),
            hub_auth_url=env("HUB_AUTH_URL", ""),
            hub_auth_timeout=env_float("HUB_AUTH_TIMEOUT", 5.0),
            robot_id=validate_robot_id(env("ROBOT_ID", DEFAULT_ROBOT_ID)),
            mqtt_client_id=os.environ.get("MQTT_CLIENT_ID"),
            http_timeout=env_float("HTTP_TIMEOUT", 5.0),
            task_poll_interval=env_float("TASK_POLL_INTERVAL", 1.0),
            task_poll_timeout=env_float("TASK_POLL_TIMEOUT", 120.0),
            local_ip=env("LOCAL_IP", HOST_LAN_IP_DEFAULT),
            service_use_local=env_bool("SERVICE_USE_LOCAL", False),
            robot_service_url=os.environ.get("ROBOT_SERVICE_URL"),
            igus_service_url=os.environ.get("IGUS_SERVICE_URL"),
            xarm_service_url=os.environ.get("XARM_SERVICE_URL"),
            symovo_service_url=os.environ.get("SYMOVO_SERVICE_URL"),
            service_map_json=os.environ.get("SERVICE_MAP_JSON"),
            internal_service_key=env("INTERNAL_SERVICE_KEY", ""),
            status_heartbeat_interval=env_float("STATUS_HEARTBEAT_INTERVAL", 15.0),
            mqtt_publish_qos=env_int("MQTT_PUBLISH_QOS", 1),
            safety_gate_startup_grace=env_float("SAFETY_GATE_STARTUP_GRACE", SAFETY_GATE_STARTUP_GRACE_SECONDS),
            log_level=env("LOG_LEVEL", "INFO"),
            long_operations_timeouts=os.environ.get("LONG_OPERATIONS_TIMEOUTS"),
        )

    def parse_long_operations(self) -> dict[str, dict[str, float]]:
        if not self.long_operations_timeouts:
            return dict(DEFAULT_LONG_OPERATIONS)
        try:
            return json.loads(self.long_operations_timeouts)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse LONG_OPERATIONS_TIMEOUTS, using defaults")
            return dict(DEFAULT_LONG_OPERATIONS)

    def warn_unconfigured_services(self) -> None:
        """Log warnings for services that have no URL configured."""
        for name, url in [
            ("robot", self.robot_service_url),
            ("igus", self.igus_service_url),
            ("xarm", self.xarm_service_url),
            ("symovo", self.symovo_service_url),
        ]:
            if not url:
                logger.warning(
                    "[config] Service '%s' has no URL configured — commands to this service will fail until configured",
                    name,
                )

    def build_service_configs(self) -> dict[str, ServiceConfig]:
        local_ip = self.local_ip
        services: dict[str, dict] = {}

        # Only register services that have a URL configured.
        _defaults: list[tuple[str, str | None, bool]] = [
            ("robot", self.robot_service_url, True),
            ("igus", self.igus_service_url, False),
            ("xarm", self.xarm_service_url, False),
            ("symovo", self.symovo_service_url, False),
        ]
        for name, url, watch in _defaults:
            if url:
                services[name] = {"base_url": url, "watch_tasks": watch}

        if self.service_use_local:
            overwritten = [name for name in ("robot", "igus", "xarm", "symovo") if name in services]
            if overwritten:
                logger.warning(
                    "[config] SERVICE_USE_LOCAL=true overrides explicit URLs for: %s",
                    ", ".join(overwritten),
                )
            services.update(
                {
                    "robot": {"base_url": f"http://{local_ip}:{SERVICE_PORT_ROBOT}", "watch_tasks": True},
                    "igus": {"base_url": f"http://{local_ip}:{SERVICE_PORT_IGUS}"},
                    "xarm": {"base_url": f"http://{local_ip}:{SERVICE_PORT_XARM}"},
                    "symovo": {"base_url": f"http://{local_ip}:{SERVICE_PORT_SYMOVO}"},
                }
            )

        if self.service_map_json:
            try:
                override = json.loads(self.service_map_json)
                if isinstance(override, dict):
                    overwritten = [name for name in override if name in services]
                    if overwritten:
                        logger.warning(
                            "[config] SERVICE_MAP_JSON overrides existing URLs for: %s",
                            ", ".join(overwritten),
                        )
                    for name, cfg in override.items():
                        if isinstance(cfg, dict) and "base_url" in cfg:
                            base_url = cfg["base_url"]
                        elif isinstance(cfg, str):
                            base_url = cfg
                        else:
                            logger.warning("[config] SERVICE_MAP_JSON: invalid entry for '%s', skipping", name)
                            continue

                        parsed = urlparse(str(base_url))
                        if parsed.scheme not in ("http", "https"):
                            logger.warning(
                                "[config] SERVICE_MAP_JSON: rejecting '%s' — scheme '%s' not allowed (only http/https)",
                                name,
                                parsed.scheme,
                            )
                            continue

                        watch_tasks = bool(cfg.get("watch_tasks", False)) if isinstance(cfg, dict) else False
                        services[name] = {"base_url": base_url, "watch_tasks": watch_tasks}
            except json.JSONDecodeError:
                logger.warning("[config] Failed to decode SERVICE_MAP_JSON")

        result: dict[str, ServiceConfig] = {}
        for name, cfg in services.items():
            result[name] = ServiceConfig(
                name=name,
                base_url=str(cfg["base_url"]).rstrip("/"),
                watch_tasks=bool(cfg.get("watch_tasks", False)),
                allowed_path_prefixes=DEFAULT_ALLOWED_PATH_PREFIXES.get(name, DYNAMIC_SERVICE_DEFAULT_PREFIXES),
            )
        return result


_get_settings = singleton_factory(BridgeServiceSettings.from_env)


def get_settings() -> BridgeServiceSettings:
    return _get_settings()


def fetch_broker_config(settings: BridgeServiceSettings) -> BridgeConfig:
    """Fetch broker config from config-api with retry/backoff."""
    from shared.broker_fetch import fetch_config as _fetch

    def _build(data: dict[str, Any]) -> BridgeConfig:
        robot_id = settings.robot_id
        client_id = settings.mqtt_client_id or robot_id
        services = settings.build_service_configs()

        mqtt = build_mqtt_connection_config(
            data,
            robot_id,
            client_id,
            settings.mqtt_publish_qos,
        )

        return BridgeConfig(
            mqtt=mqtt,
            http_timeout=settings.http_timeout,
            task_poll_interval=settings.task_poll_interval,
            task_poll_timeout=settings.task_poll_timeout,
            services=MappingProxyType(services),
            status_heartbeat_interval=settings.status_heartbeat_interval,
            safety_gate_startup_grace=settings.safety_gate_startup_grace,
            long_operations=MappingProxyType(settings.parse_long_operations()),
        )

    return _fetch(settings.config_api_url, _build, api_key=settings.internal_service_key or None)
