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
    Import the ``settings`` singleton directly::

        from app.core.config import settings
        timeout = settings.http_connect_timeout
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
    cam_keepalive_expiry: float = Field(default=15.0, alias="CAM_KEEPALIVE_EXPIRY")
    camera_services_csv: str = Field(default="color_camera,depth_camera", alias="CAMERA_SERVICES")

    # ── Concurrency ──────────────────────────────────────────────
    default_service_concurrency: int = Field(default=10, alias="DEFAULT_SERVICE_CONCURRENCY")
    camera_service_concurrency: int = Field(default=15, alias="CAMERA_SERVICE_CONCURRENCY")

    # ── WebSocket proxy limits ───────────────────────────────────
    ws_max_concurrent: int = Field(default=30, alias="WS_MAX_CONCURRENT")
    ws_acquire_timeout: float = Field(default=5.0, alias="WS_ACQUIRE_TIMEOUT")
    ws_connect_timeout: float = Field(default=5.0, alias="WS_CONNECT_TIMEOUT")
    ws_total_timeout: float = Field(default=3600.0, alias="WS_TOTAL_TIMEOUT")
    ws_max_message_size: int = Field(default=2**20, alias="WS_MAX_MESSAGE_SIZE")
    ws_ping_interval: float = Field(default=20.0, alias="WS_PING_INTERVAL")
    ws_ping_timeout: float = Field(default=20.0, alias="WS_PING_TIMEOUT")

    # ── Proxy lifecycle ──────────────────────────────────────────
    proxy_connect_timeout: float = Field(default=8.0, alias="PROXY_CONNECT_TIMEOUT")
    proxy_body_timeout: float = Field(default=15.0, alias="PROXY_BODY_TIMEOUT")
    body_read_timeout: float = Field(default=30.0, alias="BODY_READ_TIMEOUT")

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
    gateway_admin_key: str | None = Field(
        default=None,
        alias="GATEWAY_ADMIN_KEY",
        description="If set, all mutating /api/v1/hub/* endpoints require X-Admin-Key header.",
    )

    # ── Readiness ────────────────────────────────────────────────
    readiness_check_services: bool = Field(default=True, alias="READINESS_CHECK_SERVICES")
    readiness_check_auth: bool = Field(default=True, alias="READINESS_CHECK_AUTH")
    readiness_check_timeout: float = Field(default=2.5, alias="READINESS_CHECK_TIMEOUT")


# ── Settings singleton ──────────────────────────────────────────────────────

settings = GatewaySettings()


# ── Service map builder ─────────────────────────────────────────────────────

def load_service_map() -> Dict[str, Dict[str, str]]:
    """Build service map from settings — no hardcoded IPs.

    URL resolution order (first wins):
    1. SERVICE_MAP_JSON (full override blob)
    2. Explicit per-service field in settings (SERVICE_IGUS_URL, etc.)
    3. Default from shared_config network topology
    """
    s = settings
    lip = s.host_lan_ip
    dip = s.depth_camera_ip
    base: Dict[str, Dict[str, str]] = {
        "igus":         {"url": s.service_igus_url or f"http://{lip}:{PORTS.IGUS}", "prefix": ""},
        "xarm":         {"url": s.service_xarm_url or f"http://{lip}:{PORTS.XARM}", "prefix": ""},
        "symovo":       {"url": s.service_symovo_url or f"http://{lip}:{PORTS.SYMOVO}", "prefix": ""},
        "robot":        {"url": s.service_robot_url or f"http://{lip}:{PORTS.ROBOT}", "prefix": ""},
        "color_camera": {"url": s.service_color_camera_url or f"http://{lip}:{PORTS.COLOR_CAMERA}", "prefix": ""},
        # depth_camera uses COLOR_CAMERA port intentionally: the depth node
        # runs the same janus_camera_page service on the same port as color,
        # just on a different IP (depth_camera_ip).
        "depth_camera": {"url": s.service_depth_camera_url or f"http://{dip}:{PORTS.COLOR_CAMERA}", "prefix": ""},
    }
    # SERVICE_MAP_JSON override
    raw = s.service_map_json
    if raw:
        try:
            m = json.loads(raw)
            if isinstance(m, dict):
                for k, v in m.items():
                    if isinstance(v, str):
                        if not v.startswith(("http://", "https://")):
                            logger.warning("SERVICE_MAP_JSON: skipping %s — URL must be http(s)", k)
                            continue
                        base[k] = {"url": v, "prefix": "/api/v1"}
                    elif isinstance(v, dict):
                        url = v.get("url") or v.get("base_url")
                        prefix = v.get("prefix", "/api/v1")
                        if url:
                            if not url.startswith(("http://", "https://")):
                                logger.warning("SERVICE_MAP_JSON: skipping %s — URL must be http(s)", k)
                                continue
                            base[k] = {"url": url, "prefix": prefix}
        except json.JSONDecodeError:
            logger.warning("Failed to parse SERVICE_MAP_JSON: %s", raw[:200])
    return base


# ── Derived / computed values ───────────────────────────────────────────────

SERVICE_MAP: Dict[str, Dict[str, str]] = load_service_map()

BASE_DIR = Path(__file__).resolve().parent.parent

APP_ENV = settings.app_env.strip().lower()
STRICT_RUNTIME = settings.strict_runtime if settings.strict_runtime is not None else (APP_ENV in {"prod", "production"})

CAMERA_SERVICES = frozenset(
    s.strip() for s in settings.camera_services_csv.split(",") if s.strip()
)


def _service_concurrency(name: str) -> int:
    env_key = f"SERVICE_{name.replace('-', '_').upper()}_CONCURRENCY"
    val = os.getenv(env_key)
    if val is not None:
        return int(val)
    if name in CAMERA_SERVICES:
        return settings.camera_service_concurrency
    return settings.default_service_concurrency


SERVICE_CONCURRENCY: Dict[str, int] = {
    name: _service_concurrency(name) for name in SERVICE_MAP
}

ALLOWED_ORIGINS = [
    settings.frontend_origin or f"http://{settings.public_host}:{PORTS.FRONTEND}",
    f"http://localhost:{PORTS.FRONTEND}",
    f"http://127.0.0.1:{PORTS.FRONTEND}",
]
if settings.allowed_origins_csv:
    ALLOWED_ORIGINS.extend([o.strip() for o in settings.allowed_origins_csv.split(",") if o.strip()])
ALLOWED_ORIGINS = list(dict.fromkeys([o for o in ALLOWED_ORIGINS if o]))


def validate_runtime_config() -> None:
    if not STRICT_RUNTIME:
        return

    s = settings
    errors: list[str] = []

    if not s.verify_tls:
        errors.append("VERIFY_TLS must be enabled in strict runtime")
    if s.allow_insecure_tls:
        errors.append("ALLOW_INSECURE_TLS must be disabled in strict runtime")
    if not s.readiness_check_services:
        errors.append("READINESS_CHECK_SERVICES must be enabled in strict runtime")
    if not s.readiness_check_auth:
        errors.append("READINESS_CHECK_AUTH must be enabled in strict runtime")
    if not s.gateway_admin_key:
        errors.append("GATEWAY_ADMIN_KEY must be set in strict runtime to protect management endpoints")

    _demo_api_key = os.getenv("DEMO_ROBOT_API_KEY", "")
    if _demo_api_key and s.default_robot_api_key and s.default_robot_api_key.strip() == _demo_api_key:
        errors.append("DEFAULT_ROBOT_API_KEY contains demo value")
    _demo_hub_url = os.getenv("DEMO_HUB_BASE_URL", "")
    if _demo_hub_url and s.default_hub_base_url and s.default_hub_base_url.rstrip("/") == _demo_hub_url.rstrip("/"):
        errors.append("DEFAULT_HUB_BASE_URL contains demo endpoint")

    if not s.robot_api_key_file:
        errors.append("ROBOT_API_KEY_FILE must be configured in strict runtime")
    else:
        key_file = Path(s.robot_api_key_file)
        if not key_file.is_file():
            errors.append(f"ROBOT_API_KEY_FILE does not exist: {s.robot_api_key_file}")

    if errors:
        raise RuntimeError("Invalid strict runtime configuration:\n- " + "\n- ".join(errors))

