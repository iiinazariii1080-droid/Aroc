"""Canonical service ports, device defaults, and URL resolution.

Every micro-service has a well-known port.  ``get_service_url`` resolves
a service base URL by checking an env var first, then falling back to
``http://<host>:<port>`` where *host* defaults to the Docker DNS name
(for compose) or ``HOST_LAN_IP`` (for bare-metal).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ── Well-known ports ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServicePorts:
    """Canonical port numbers — single source of truth."""
    IGUS: int = 8101
    XARM: int = 8102
    ROBOT: int = 8110
    SYMOVO: int = 7905
    TELEOP: int = 7906
    MQTT_API: int = 7900
    API_GATEWAY: int = 8201
    COLOR_CAMERA: int = 8900
    DEPTH_PROXY: int = 9000
    FRONTEND: int = 8401
    JANUS_HTTP: int = 8088
    JANUS_WS: int = 8188
    XARM_WS: int = 18333


PORTS = ServicePorts()


# ── Physical device defaults (LAN IPs) ─────────────────────────────────────

@dataclass(frozen=True)
class DeviceDefaults:
    """Default LAN addresses for physical hardware.

    These are sensible defaults for the current network topology.
    Always overridable via env vars.
    """
    HOST_LAN_IP: str = "192.168.1.10"
    XARM_IP: str = "192.168.1.220"
    IGUS_MOTOR_IP: str = "192.168.1.230"
    DEPTH_CAMERA_IP: str = "192.168.1.55"
    SYMOVO_CAR_IP: str = "192.168.1.100"
    MQTT_BROKER_HOST: str = "82.165.177.194"
    TURN_HOST: str = "82.165.177.194"


DEVICES = DeviceDefaults()


# ── Service registry ────────────────────────────────────────────────────────

# Maps a service *name* to (env-var for full URL override, Docker DNS name, port).
_SERVICE_REGISTRY: dict[str, tuple[str, str, int]] = {
    "igus":         ("IGUS_SERVICE_URL",         "igus-service",         PORTS.IGUS),
    "xarm":         ("XARM_SERVICE_URL",         "xarm-service",         PORTS.XARM),
    "robot":        ("ROBOT_SERVICE_URL",        "robot-service",        PORTS.ROBOT),
    "symovo":       ("SYMOVO_SERVICE_URL",       "symovo-service",       PORTS.SYMOVO),
    "mqtt":         ("MQTT_SERVICE_URL",         "mqtt-service",         PORTS.MQTT_API),
    "api_gateway":  ("API_GATEWAY_URL",          "api-gateway",          PORTS.API_GATEWAY),
    "color_camera": ("COLOR_CAMERA_SERVICE_URL", "color-camera-service", PORTS.COLOR_CAMERA),
    "depth_proxy":  ("DEPTH_PROXY_SERVICE_URL",  "depth-proxy-service",  PORTS.DEPTH_PROXY),
    "frontend":     ("FRONTEND_SERVICE_URL",     "frontend-service",     PORTS.FRONTEND),
}


def get_service_url(name: str, *, host: str | None = None) -> str:
    """Resolve the base URL for a named service.

    Resolution order:
    1. Dedicated env var (e.g. ``IGUS_SERVICE_URL``)
    2. ``http://<host>:<port>`` where *host* comes from:
       a. the *host* argument,
       b. ``HOST_LAN_IP`` env var,
       c. ``DeviceDefaults.HOST_LAN_IP``.
    """
    if name not in _SERVICE_REGISTRY:
        raise KeyError(f"Unknown service: {name!r}. Known: {sorted(_SERVICE_REGISTRY)}")

    env_key, _docker_name, port = _SERVICE_REGISTRY[name]
    override = os.getenv(env_key)
    if override:
        return override.rstrip("/")

    effective_host = host or os.getenv("HOST_LAN_IP", DEVICES.HOST_LAN_IP)
    return f"http://{effective_host}:{port}"
