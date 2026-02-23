from pathlib import Path
from typing import Dict
import json
import logging
import os

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def load_service_map() -> Dict[str, Dict[str, str]]:
    """
    Статическая конфигурация сервисов:
      - url: базовый адрес HTTP сервиса
      - prefix: префикс его API
    Внешний маршрут:  /api/v1/{service}/{path}
    Прокси на апстрим: {url}{prefix}/{path}
    """
    local_ip = os.environ.get("LOCAL_IP", "192.168.1.10")
    depth_camera_ip = os.environ.get("DEPTH_CAMERA_IP", "192.168.1.55")
    base: Dict[str, Dict[str, str]] = {
        "igus": {"url": f"http://{local_ip}:8101", "prefix": ""},
        "xarm": {"url": f"http://{local_ip}:8102", "prefix": ""},
        "symovo": {"url": f"http://{local_ip}:7905", "prefix": ""},
        "robot": {"url": f"http://{local_ip}:8110", "prefix": ""},
        "color_camera": {"url": f"http://{local_ip}:8900", "prefix": ""},
        "depth_camera": {"url": f"http://{depth_camera_ip}:8900", "prefix": ""},
    }
    # ENV overrides per service
    for key, cfg in base.items():
        env_key = key.replace("-", "_").upper()
        url_override = os.getenv(f"SERVICE_{env_key}_URL")
        if url_override:
            cfg["url"] = url_override
        prefix_override = os.getenv(f"SERVICE_{env_key}_PREFIX")
        if prefix_override is not None:
            cfg["prefix"] = prefix_override
    # SERVICE_MAP_JSON='{"igus":{"url":"http://...","prefix":"/api/v1"}, "newsvc":"http://..."}'
    raw = os.getenv("SERVICE_MAP_JSON")
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


SERVICE_MAP: Dict[str, Dict[str, str]] = load_service_map()

APP_ENV = os.getenv("APP_ENV", "dev").strip().lower()
STRICT_RUNTIME = _bool_env("STRICT_RUNTIME", APP_ENV in {"prod", "production"})

VERIFY_TLS = _bool_env("VERIFY_TLS", True)

CONNECT_TIMEOUT_S = float(os.getenv("HTTP_CONNECT_TIMEOUT", "2.0"))
READ_TIMEOUT_S = float(os.getenv("HTTP_READ_TIMEOUT", "5.0"))
WRITE_TIMEOUT_S = float(os.getenv("HTTP_WRITE_TIMEOUT", "10.0"))
POOL_TIMEOUT_S = float(os.getenv("HTTP_POOL_TIMEOUT", "3.0"))

MAX_CONNECTIONS = int(os.getenv("HTTP_MAX_CONNECTIONS", "30"))
MAX_KEEPALIVE_CONNECTIONS = int(os.getenv("HTTP_MAX_KEEPALIVE_CONNECTIONS", "15"))
KEEPALIVE_EXPIRY_S = float(os.getenv("HTTP_KEEPALIVE_EXPIRY", "30.0"))

# WebSocket backend addresses (host:port/path)
WS_XARM_URL = os.getenv("WS_XARM_URL", "ws://192.168.1.220:18333/ws")
WS_COLOR_CAMERA_URL = os.getenv("WS_COLOR_CAMERA_URL", "ws://192.168.1.10:8188/janus-ws")
WS_DEPTH_CAMERA_URL = os.getenv("WS_DEPTH_CAMERA_URL", "ws://192.168.1.55:8188/janus-ws")

RETRY_ATTEMPTS = int(os.getenv("HTTP_RETRY_ATTEMPTS", "1"))
RETRY_BACKOFF_S = float(os.getenv("HTTP_RETRY_BACKOFF", "0.15"))

# ── Camera-specific HTTP client (isolated pool) ────────────────
CAM_CONNECT_TIMEOUT_S = float(os.getenv("CAM_CONNECT_TIMEOUT", "2.0"))
CAM_READ_TIMEOUT_S = float(os.getenv("CAM_READ_TIMEOUT", "8.0"))
CAM_WRITE_TIMEOUT_S = float(os.getenv("CAM_WRITE_TIMEOUT", "8.0"))
CAM_POOL_TIMEOUT_S = float(os.getenv("CAM_POOL_TIMEOUT", "2.0"))
CAM_MAX_CONNECTIONS = int(os.getenv("CAM_MAX_CONNECTIONS", "20"))
CAM_MAX_KEEPALIVE = int(os.getenv("CAM_MAX_KEEPALIVE", "10"))

# Services that use the camera HTTP client instead of the main one
CAMERA_SERVICES = frozenset(s.strip() for s in os.getenv(
    "CAMERA_SERVICES", "color_camera,depth_camera"
).split(",") if s.strip())

# ── Per-service concurrency (asyncio.Semaphore) ────────────────
# Max in-flight proxy requests per service. Prevents one slow service
# from monopolizing the single worker.
DEFAULT_SERVICE_CONCURRENCY = int(os.getenv("DEFAULT_SERVICE_CONCURRENCY", "10"))
CAMERA_SERVICE_CONCURRENCY = int(os.getenv("CAMERA_SERVICE_CONCURRENCY", "15"))

# Per-service concurrency overrides: SERVICE_<NAME>_CONCURRENCY=N
# e.g. SERVICE_SYMOVO_CONCURRENCY=5
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
# 10 clients × 2 streams (color+depth) + xarm = ~21, set to 30 with headroom
WS_MAX_CONCURRENT = int(os.getenv("WS_MAX_CONCURRENT", "30"))
WS_ACQUIRE_TIMEOUT_S = float(os.getenv("WS_ACQUIRE_TIMEOUT", "5.0"))  # wait for a slot before rejecting
WS_CONNECT_TIMEOUT_S = float(os.getenv("WS_CONNECT_TIMEOUT", "5.0"))
WS_TOTAL_TIMEOUT_S = float(os.getenv("WS_TOTAL_TIMEOUT", "3600.0"))  # 1 hour max per WS session

# ── HTTP proxy request lifecycle ──────────────────────────────
# Connect timeout covers semaphore acquisition + upstream connection + headers.
PROXY_CONNECT_TIMEOUT_S = float(os.getenv("PROXY_CONNECT_TIMEOUT", "8.0"))
# Body timeout covers streaming the response body back to the client.
PROXY_BODY_TIMEOUT_S = float(os.getenv("PROXY_BODY_TIMEOUT", "15.0"))

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

DEFAULT_HUB_BASE_URL = os.getenv("DEFAULT_HUB_BASE_URL")
DEFAULT_ROBOT_ID = os.getenv("DEFAULT_ROBOT_ID")
DEFAULT_ROBOT_API_KEY = os.getenv("DEFAULT_ROBOT_API_KEY")
DEFAULT_ROBOT_DISPLAY_NAME = os.getenv("DEFAULT_ROBOT_DISPLAY_NAME")
ROBOT_API_KEY_FILE = os.getenv("ROBOT_API_KEY_FILE")

PUBLIC_HOST = os.getenv("PUBLIC_HOST", "192.168.1.10")

ALLOWED_ORIGINS = [
    os.getenv("FRONTEND_ORIGIN", f"http://{PUBLIC_HOST}:8401"),
    "http://localhost:8401",
    "http://127.0.0.1:8401",
]
_extra_origins = os.getenv("ALLOWED_ORIGINS")
if _extra_origins:
    ALLOWED_ORIGINS.extend([o.strip() for o in _extra_origins.split(",") if o.strip()])
ALLOWED_ORIGINS = list(dict.fromkeys([o for o in ALLOWED_ORIGINS if o]))

ALLOW_INSECURE_TLS = os.getenv("ALLOW_INSECURE_TLS", "0") == "1"

MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024 * 1024)))  # 50 MB

READINESS_CHECK_SERVICES = _bool_env("READINESS_CHECK_SERVICES", True)
READINESS_CHECK_AUTH = _bool_env("READINESS_CHECK_AUTH", True)
READINESS_CHECK_TIMEOUT_S = float(os.getenv("READINESS_CHECK_TIMEOUT", "2.5"))


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

