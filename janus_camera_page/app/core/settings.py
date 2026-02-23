from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Settings:
    app_title: str = "cam-control"
    app_version: str = "1.0"
    base_dir: Path = PROJECT_DIR
    templates_dir: Path = PROJECT_DIR / "templates"
    static_dir: Path = PROJECT_DIR / "static"
    env_path: Path = Path(os.environ.get("CAM_ENV_PATH", "/etc/robot/cam-rgb.env"))
    lock_path: Path = Path(os.environ.get("CAM_ENV_LOCK_PATH", "/tmp/cam-rgb.env.lock"))
    camera_device: str = os.environ.get("CAM_DEVICE", "/dev/cam-rgb")
    camera_type: str = os.environ.get("CAM_TYPE", "color_camera")
    service_name: str = os.environ.get("CAM_SERVICE", "rtp-rgb@cam-rgb.service")
    api_key: str | None = os.environ.get("CAMCTRL_API_KEY")
    snapshot_path: str = os.environ.get("SNAPSHOT_PATH", "/run/cam-rgb/snapshot.jpg")
    janus_url: str = os.environ.get("JANUS_URL", "http://127.0.0.1:8088/janus").rstrip("/")
    janus_timeout: float = float(os.environ.get("JANUS_TIMEOUT", "3"))
    janus_mount_id: int = int(os.environ.get("JANUS_MOUNT_ID", "1305"))
    janus_http_base: str = os.environ.get("JANUS_HTTP", "http://127.0.0.1:8088")
    relay_url: str = os.environ.get("RELAY_URL", "http://127.0.0.1:9000").rstrip("/")
    allow_insecure_tls: bool = os.environ.get("ALLOW_INSECURE_TLS", "0") == "1"
    turn_host: str = os.getenv("TURN_HOST", "82.165.177.194")
    turn_port: int = int(os.getenv("TURN_PORT", "3478"))
    turn_user: str = os.getenv("TURN_USER", "webrtc")
    turn_pass: str = os.getenv("TURN_PASS", "G456AH37gbc")
    ice_policy: str = os.getenv("ICE_POLICY", "all")
    watchdog_enabled: bool = os.environ.get("CAM_WATCHDOG", "0") == "1"
    snapshot_watchdog_enabled: bool = os.environ.get("CAM_WATCHDOG", "0") == "1"
    watchdog_interval_sec: int = int(os.environ.get("CAM_WATCHDOG_INTERVAL", "10"))
    watchdog_stale_ms: int = int(os.environ.get("CAM_WATCHDOG_STALE_MS", "5000"))
    cors_origins: List[str] = field(
        default_factory=lambda: [
            "http://192.168.1.101:*",
            "http://localhost:*",
            "http://127.0.0.1:*",
            "https://*.techvisioncloud.pl",
        ]
    )
    janus_ws_backends: Dict[str, str] = field(
        default_factory=lambda: {
            "1": os.getenv("JANUS_WS_URL_1", "ws://127.0.0.1:8188/janus-ws"),
        }
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

