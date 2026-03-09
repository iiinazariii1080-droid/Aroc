from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared_config.network import DEVICES, PORTS

logger = logging.getLogger(__name__)


# ── Settings class ──────────────────────────────────────────────────────────

class GatewaySettings(BaseSettings):
    """API Gateway configuration — single source of truth.

    All values are loaded from env vars (with ``.env`` fallback).
    Module-level constants below are derived from an instance for
    backward compatibility with ``from app.core.config import X``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Network topology ─────────────────────────────────────────
    host_lan_ip: str = Field(default=DEVICES.HOST_LAN_IP, alias="HOST_LAN_IP")
    depth_camera_ip: str = Field(default=DEVICES.DEPTH_CAMERA_IP, alias="DEPTH_CAMERA_IP")
    public_host: str = Field(default=DEVICES.HOST_LAN_IP, alias="PUBLIC_HOST")
    app_env: str = Field(default="dev", alias="APP_ENV")

    # ── Service URL overrides (optional) ─────────────────────────
    service_igus_url: str | None = Field(default=None, alias="SERVICE_IGUS_URL")
    service_xarm_url: str | None = Field(default=None, alias="SERVICE_XARM_URL")
    service_symovo_url: str | None = Field(default=None, alias="SERVICE_SYMOVO_URL")
    service_robot_url: str | None = Field(default=None, alias="SERVICE_ROBOT_URL")
    service_color_camera_url: str | None = Field(default=None, alias="SERVICE_COLOR_CAMERA_URL")
    service_depth_camera_url: str | None = Field(default=None, alias="SERVICE_DEPTH_CAMERA_URL")
    service_map_json: str | None = Field(default=None, alias="SERVICE_MAP_JSON")

    # ── WebSocket backends ───────────────────────────────────────
    ws_xarm_url: str = Field(
        default=f"ws://{DEVICES.XARM_IP}:{PORTS.XARM_WS}/ws",
        alias="WS_XARM_URL",
    )
    ws_color_camera_url: str = Field(
        default=f"ws://{DEVICES.HOST_LAN_IP}:{PORTS.JANUS_WS}/janus-ws",
        alias="WS_COLOR_CAMERA_URL",
    )
    ws_depth_camera_url: str = Field(
        default=f"ws://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.JANUS_WS}/janus-ws",
        alias="WS_DEPTH_CAMERA_URL",
    )

    # ── HTTP client tuning ───────────────────────────────────────
    verify_tls: bool = Field(default=True, alias="VERIFY_TLS")
    http_connect_timeout: float = Field(default=2.0, alias="HTTP_CONNECT_TIMEOUT")
    http_read_timeout: float = Field(default=5.0, alias="HTTP_READ_TIMEOUT")
    http_write_timeout: float = Field(default=10.0, alias="HTTP_WRITE_TIMEOUT")
    http_pool_timeout: float = Field(default=3.0, alias="HTTP_POOL_TIMEOUT")
    http_max_connections: int = Field(default=30, alias="HTTP_MAX_CONNECTIONS")
    http_max_keepalive_connections: int = Field(default=15, alias="HTTP_MAX_KEEPALIVE_CONNECTIONS")
    http_keepalive_expiry: float = Field(default=30.0, alias="HTTP_KEEPALIVE_EXPIRY")
    http_retry_attempts: int = Field(default=1, alias="HTTP_RETRY_ATTEMPTS")
    http_retry_backoff: float = Field(default=0.15, alias="HTTP_RETRY_BACKOFF")

    # ── Camera HTTP client (isolated pool) ───────────────────────
    cam_connect_timeout: float = Field(default=2.0, alias="CAM_CONNECT_TIMEOUT")
    cam_read_timeout: float = Field(default=30.0, alias="CAM_READ_TIMEOUT")
    cam_write_timeout: float = Field(default=8.0, alias="CAM_WRITE_TIMEOUT")
    cam_pool_timeout: float = Field(default=2.0, alias="CAM_POOL_TIMEOUT")
    cam_max_connections: int = Field(default=20, alias="CAM_MAX_CONNECTIONS")
    cam_max_keepalive: int = Field(default=10, alias="CAM_MAX_KEEPALIVE")
    camera_services_csv: str = Field(default="color_camera,depth_camera", alias="CAMERA_SERVICES")

    # ── Concurrency ──────────────────────────────────────────────
    default_service_concurrency: int = Field(default=10, alias="DEFAULT_SERVICE_CONCURRENCY")
    camera_service_concurrency: int = Field(default=15, alias="CAMERA_SERVICE_CONCURRENCY")

    # ── WebSocket proxy limits ───────────────────────────────────
    ws_max_concurrent: int = Field(default=30, alias="WS_MAX_CONCURRENT")
    ws_acquire_timeout: float = Field(default=5.0, alias="WS_ACQUIRE_TIMEOUT")
    ws_connect_timeout: float = Field(default=5.0, alias="WS_CONNECT_TIMEOUT")
    ws_total_timeout: float = Field(default=3600.0, alias="WS_TOTAL_TIMEOUT")

    # ── Proxy lifecycle ──────────────────────────────────────────
    proxy_connect_timeout: float = Field(default=8.0, alias="PROXY_CONNECT_TIMEOUT")
    proxy_body_timeout: float = Field(default=15.0, alias="PROXY_BODY_TIMEOUT")

    # ── Hub / auth ───────────────────────────────────────────────
    default_hub_base_url: str | None = Field(default=None, alias="DEFAULT_HUB_BASE_URL")
    default_robot_id: str | None = Field(default=None, alias="DEFAULT_ROBOT_ID")
    default_robot_api_key: str | None = Field(default=None, alias="DEFAULT_ROBOT_API_KEY")
    default_robot_display_name: str | None = Field(default=None, alias="DEFAULT_ROBOT_DISPLAY_NAME")
    robot_api_key_file: str | None = Field(default=None, alias="ROBOT_API_KEY_FILE")

    # ── CORS / security ──────────────────────────────────────────
    frontend_origin: str | None = Field(default=None, alias="FRONTEND_ORIGIN")
    allowed_origins_csv: str | None = Field(default=None, alias="ALLOWED_ORIGINS")
    allow_insecure_tls: bool = Field(default=False, alias="ALLOW_INSECURE_TLS")
    strict_runtime: bool | None = Field(default=None, alias="STRICT_RUNTIME")
    max_request_body_bytes: int = Field(default=50 * 1024 * 1024, alias="MAX_REQUEST_BODY_BYTES")

    # ── Readiness ────────────────────────────────────────────────
    readiness_check_services: bool = Field(default=True, alias="READINESS_CHECK_SERVICES")
    readiness_check_auth: bool = Field(default=True, alias="READINESS_CHECK_AUTH")
    readiness_check_timeout: float = Field(default=2.5, alias="READINESS_CHECK_TIMEOUT")


# ── Instantiate settings ────────────────────────────────────────────────────

_settings = GatewaySettings()


# ── Service map builder ─────────────────────────────────────────────────────

def load_service_map() -> Dict[str, Dict[str, str]]:
    """Build service map from settings — no hardcoded IPs."""
    s = _settings
    lip = s.host_lan_ip
    dip = s.depth_camera_ip
    base: Dict[str, Dict[str, str]] = {
        "igus":         {"url": s.service_igus_url or f"http://{lip}:{PORTS.IGUS}", "prefix": ""},
        "xarm":         {"url": s.service_xarm_url or f"http://{lip}:{PORTS.XARM}", "prefix": ""},
        "symovo":       {"url": s.service_symovo_url or f"http://{lip}:{PORTS.SYMOVO}", "prefix": ""},
        "robot":        {"url": s.service_robot_url or f"http://{lip}:{PORTS.ROBOT}", "prefix": ""},
        "color_camera": {"url": s.service_color_camera_url or f"http://{lip}:{PORTS.COLOR_CAMERA}", "prefix": ""},
        "depth_camera": {"url": s.service_depth_camera_url or f"http://{dip}:{PORTS.COLOR_CAMERA}", "prefix": ""},
    }
    # ENV overrides per service (legacy SERVICE_<NAME>_URL pattern)
    for key, cfg in base.items():
        env_key = key.replace("-", "_").upper()
        url_override = os.getenv(f"SERVICE_{env_key}_URL")
        if url_override:
            cfg["url"] = url_override
        prefix_override = os.getenv(f"SERVICE_{env_key}_PREFIX")
        if prefix_override is not None:
            cfg["prefix"] = prefix_override
    # SERVICE_MAP_JSON override
    raw = s.service_map_json
    if raw:
        try:
            m = json.loads(raw)
            if isinstance(m, dict):
                for k, v in m.items():
                    if isinstance(v, str):
                        base[k] = {"url": v, "prefix": "/api/v1"}
                    elif isinstance(v, dict):
                        url = v.get("url") or v.get("base_url")
                        prefix = v.get("prefix", "/api/v1")
                        if url:
                            base[k] = {"url": url, "prefix": prefix}
        except json.JSONDecodeError:
            logger.warning("Failed to parse SERVICE_MAP_JSON: %s", raw[:200])
    return base


# ── Module-level constants (backward compat) ────────────────────────────────

SERVICE_MAP: Dict[str, Dict[str, str]] = load_service_map()

APP_ENV = _settings.app_env.strip().lower()
STRICT_RUNTIME = _settings.strict_runtime if _settings.strict_runtime is not None else (APP_ENV in {"prod", "production"})

VERIFY_TLS = _settings.verify_tls

CONNECT_TIMEOUT_S = _settings.http_connect_timeout
READ_TIMEOUT_S = _settings.http_read_timeout
WRITE_TIMEOUT_S = _settings.http_write_timeout
POOL_TIMEOUT_S = _settings.http_pool_timeout

MAX_CONNECTIONS = _settings.http_max_connections
MAX_KEEPALIVE_CONNECTIONS = _settings.http_max_keepalive_connections
KEEPALIVE_EXPIRY_S = _settings.http_keepalive_expiry

WS_XARM_URL = _settings.ws_xarm_url
WS_COLOR_CAMERA_URL = _settings.ws_color_camera_url
WS_DEPTH_CAMERA_URL = _settings.ws_depth_camera_url

RETRY_ATTEMPTS = _settings.http_retry_attempts
RETRY_BACKOFF_S = _settings.http_retry_backoff

# ── Camera-specific HTTP client (isolated pool) ────────────────
CAM_CONNECT_TIMEOUT_S = _settings.cam_connect_timeout
CAM_READ_TIMEOUT_S = _settings.cam_read_timeout
CAM_WRITE_TIMEOUT_S = _settings.cam_write_timeout
CAM_POOL_TIMEOUT_S = _settings.cam_pool_timeout
CAM_MAX_CONNECTIONS = _settings.cam_max_connections
CAM_MAX_KEEPALIVE = _settings.cam_max_keepalive

CAMERA_SERVICES = frozenset(
    s.strip() for s in _settings.camera_services_csv.split(",") if s.strip()
)

# ── Per-service concurrency (asyncio.Semaphore) ────────────────
DEFAULT_SERVICE_CONCURRENCY = _settings.default_service_concurrency
CAMERA_SERVICE_CONCURRENCY = _settings.camera_service_concurrency


def _service_concurrency(name: str) -> int:
    env_key = f"SERVICE_{name.replace('-', '_').upper()}_CONCURRENCY"
    val = os.getenv(env_key)
    if val is not None:
        return int(val)
    if name in CAMERA_SERVICES:
        return CAMERA_SERVICE_CONCURRENCY
    return DEFAULT_SERVICE_CONCURRENCY


SERVICE_CONCURRENCY: Dict[str, int] = {
    name: _service_concurrency(name) for name in SERVICE_MAP
}

# ── WebSocket proxy limits ─────────────────────────────────────
WS_MAX_CONCURRENT = _settings.ws_max_concurrent
WS_ACQUIRE_TIMEOUT_S = _settings.ws_acquire_timeout
WS_CONNECT_TIMEOUT_S = _settings.ws_connect_timeout
WS_TOTAL_TIMEOUT_S = _settings.ws_total_timeout

# ── HTTP proxy request lifecycle ──────────────────────────────
PROXY_CONNECT_TIMEOUT_S = _settings.proxy_connect_timeout
PROXY_BODY_TIMEOUT_S = _settings.proxy_body_timeout

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_HUB_BASE_URL = _settings.default_hub_base_url
DEFAULT_ROBOT_ID = _settings.default_robot_id
DEFAULT_ROBOT_API_KEY = _settings.default_robot_api_key
DEFAULT_ROBOT_DISPLAY_NAME = _settings.default_robot_display_name
ROBOT_API_KEY_FILE = _settings.robot_api_key_file

PUBLIC_HOST = _settings.public_host

ALLOWED_ORIGINS = [
    _settings.frontend_origin or f"http://{PUBLIC_HOST}:{PORTS.FRONTEND}",
    f"http://localhost:{PORTS.FRONTEND}",
    f"http://127.0.0.1:{PORTS.FRONTEND}",
]
if _settings.allowed_origins_csv:
    ALLOWED_ORIGINS.extend([o.strip() for o in _settings.allowed_origins_csv.split(",") if o.strip()])
ALLOWED_ORIGINS = list(dict.fromkeys([o for o in ALLOWED_ORIGINS if o]))

ALLOW_INSECURE_TLS = _settings.allow_insecure_tls

MAX_REQUEST_BODY_BYTES = _settings.max_request_body_bytes

READINESS_CHECK_SERVICES = _settings.readiness_check_services
READINESS_CHECK_AUTH = _settings.readiness_check_auth
READINESS_CHECK_TIMEOUT_S = _settings.readiness_check_timeout


def validate_runtime_config() -> None:
    if not STRICT_RUNTIME:
        return

    errors: list[str] = []

    if not VERIFY_TLS:
        errors.append("VERIFY_TLS must be enabled in strict runtime")
    if ALLOW_INSECURE_TLS:
        errors.append("ALLOW_INSECURE_TLS must be disabled in strict runtime")
    if not READINESS_CHECK_SERVICES:
        errors.append("READINESS_CHECK_SERVICES must be enabled in strict runtime")
    if not READINESS_CHECK_AUTH:
        errors.append("READINESS_CHECK_AUTH must be enabled in strict runtime")

    if DEFAULT_ROBOT_API_KEY and DEFAULT_ROBOT_API_KEY.strip() == "api-key-123":
        errors.append("DEFAULT_ROBOT_API_KEY contains demo value 'api-key-123'")
    if DEFAULT_HUB_BASE_URL and DEFAULT_HUB_BASE_URL.rstrip("/") == "http://82.165.177.194:8000":
        errors.append("DEFAULT_HUB_BASE_URL contains demo endpoint")

    if not ROBOT_API_KEY_FILE:
        errors.append("ROBOT_API_KEY_FILE must be configured in strict runtime")
    else:
        key_file = Path(ROBOT_API_KEY_FILE)
        if not key_file.is_file():
            errors.append(f"ROBOT_API_KEY_FILE does not exist: {ROBOT_API_KEY_FILE}")

    if errors:
        raise RuntimeError("Invalid strict runtime configuration:\n- " + "\n- ".join(errors))

