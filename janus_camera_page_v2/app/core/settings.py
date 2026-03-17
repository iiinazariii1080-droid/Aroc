from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from app.config import DEVICES, PORTS


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


# ── Helpers to keep field definitions concise ───────────────────────
def env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: str) -> int:
    return int(os.environ.get(key, default))


def _env_float(key: str, default: str) -> float:
    return float(os.environ.get(key, default))


def _env_bool(key: str, default: str = "0") -> bool:
    return os.environ.get(key, default) == "1"


def _env_path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default))


@dataclass(frozen=True)
class Settings:
    """Application settings.

    All environment-variable-dependent fields use ``default_factory`` so
    that values are read at *instance creation* time, not at class
    definition / module import time.  This ensures ``get_settings.cache_clear()``
    in tests actually picks up env-var overrides set after import.
    """

    app_title: str = "cam-control"
    app_version: str = "1.0"
    base_dir: Path = PROJECT_DIR
    templates_dir: Path = field(default_factory=lambda: PROJECT_DIR / "templates")
    static_dir: Path = field(default_factory=lambda: PROJECT_DIR / "static")

    # ── Paths & devices ──
    env_path: Path = field(default_factory=lambda: _env_path("CAM_ENV_PATH", "/etc/robot/cam-rgb.env"))
    lock_path: Path = field(default_factory=lambda: _env_path("CAM_ENV_LOCK_PATH", "/run/camera/cam-rgb.env.lock"))
    camera_device: str = field(default_factory=lambda: env_str("CAM_DEVICE", "/dev/cam-rgb"))
    camera_type: str = field(default_factory=lambda: env_str("CAM_TYPE", "color_camera"))
    service_name: str = field(default_factory=lambda: env_str("CAM_SERVICE", "rtp-rgb@cam-rgb.service"))
    # Configurable systemd unit names used by the FDIR recovery ladder.
    janus_service_name: str = field(default_factory=lambda: env_str("JANUS_SERVICE_NAME", "janus.service"))
    realsense_failsafe_service_name: str = field(
        default_factory=lambda: env_str("REALSENSE_FAILSAFE_SERVICE_NAME", "realsense-failsafe.service")
    )

    # ── Auth ──
    api_key: str | None = field(default_factory=lambda: os.environ.get("CAMCTRL_API_KEY"))
    admin_token: str = field(default_factory=lambda: env_str("CAM_ADMIN_TOKEN", ""))
    admin_enforce: bool = field(default_factory=lambda: _env_bool("CAM_ADMIN_ENFORCE", "1"))

    # ── Snapshot / media ──
    snapshot_path: str = field(default_factory=lambda: env_str("SNAPSHOT_PATH", "/run/cam-rgb/snapshot.jpg"))

    # ── Janus ──
    janus_url: str = field(
        default_factory=lambda: env_str("JANUS_URL", f"http://127.0.0.1:{PORTS.JANUS_HTTP}/janus").rstrip("/")
    )
    janus_timeout: float = field(default_factory=lambda: _env_float("JANUS_TIMEOUT", "3"))
    janus_mount_id: int = field(default_factory=lambda: _env_int("JANUS_MOUNT_ID", "1305"))
    janus_http_base: str = field(
        default_factory=lambda: env_str("JANUS_HTTP", f"http://127.0.0.1:{PORTS.JANUS_HTTP}")
    )
    janus_cfg_path: Path = field(
        default_factory=lambda: _env_path("JANUS_CFG_PATH", "/opt/janus/etc/janus/janus.jcfg")
    )
    janus_nat_json: Path = field(
        default_factory=lambda: _env_path("JANUS_NAT_JSON", "/etc/robot/janus-nat.json")
    )
    nat_config_ttl_sec: float = field(default_factory=lambda: _env_float("NAT_CONFIG_TTL_SEC", "30"))
    depth_cam_janus_ws_path: str = field(
        default_factory=lambda: env_str("DEPTH_CAM_JANUS_WS_PATH", "/janus-ws")
    )
    janus_ws_backends: Dict[str, str] = field(
        default_factory=lambda: {
            "1": os.getenv("JANUS_WS_URL_1", f"ws://127.0.0.1:{PORTS.JANUS_WS}/janus-ws"),
        }
    )

    # ── Upstream URLs ──
    relay_url: str = field(
        default_factory=lambda: env_str("RELAY_URL", f"http://127.0.0.1:{PORTS.DEPTH_PROXY}").rstrip("/")
    )
    depth_cam_url: str = field(
        default_factory=lambda: env_str(
            "DEPTH_CAM_URL", f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}"
        ).rstrip("/")
    )
    realsense_mux_url: str = field(
        default_factory=lambda: env_str("REALSENSE_MUX_URL", "http://127.0.0.1:8000").rstrip("/")
    )
    allow_insecure_tls: bool = field(default_factory=lambda: _env_bool("ALLOW_INSECURE_TLS"))

    # ── TURN / ICE ──
    turn_host: str = field(default_factory=lambda: env_str("TURN_HOST", DEVICES.TURN_HOST))
    turn_port: int = field(default_factory=lambda: _env_int("TURN_PORT", "3478"))
    turn_tls_port: int = field(default_factory=lambda: _env_int("TURN_TLS_PORT", "443"))
    turn_user: str = field(default_factory=lambda: env_str("TURN_USER", "webrtc"))
    turn_pass: str = field(default_factory=lambda: env_str("TURN_PASS", ""))
    turn_shared_secret: str = field(default_factory=lambda: env_str("TURN_SHARED_SECRET", ""))
    turn_cred_ttl: int = field(default_factory=lambda: _env_int("TURN_CRED_TTL", "86400"))
    ice_policy: str = field(default_factory=lambda: env_str("ICE_POLICY", "all"))

    # ── Watchdog ──
    watchdog_enabled: bool = field(default_factory=lambda: _env_bool("CAM_WATCHDOG", "1"))
    snapshot_watchdog_enabled: bool = field(
        default_factory=lambda: os.environ.get(
            "CAM_SNAPSHOT_WATCHDOG", os.environ.get("CAM_WATCHDOG", "1")
        ) == "1"
    )
    watchdog_interval_sec: int = field(default_factory=lambda: _env_int("CAM_WATCHDOG_INTERVAL", "8"))
    watchdog_stale_ms: int = field(default_factory=lambda: _env_int("CAM_WATCHDOG_STALE_MS", "10000"))
    watchdog_grace_sec: int = field(default_factory=lambda: _env_int("WATCHDOG_GRACE_SEC", "60"))
    watchdog_nominal_checks: int = field(default_factory=lambda: _env_int("WATCHDOG_NOMINAL_CHECKS", "10"))
    watchdog_reboot_enabled: bool = field(default_factory=lambda: _env_bool("CAM_WATCHDOG_REBOOT_ENABLED", "1"))
    max_fdir_reboots: int = field(default_factory=lambda: _env_int("MAX_FDIR_REBOOTS", "2"))

    # ── CSP ──
    csp_frame_ancestors_lan: str = field(
        default_factory=lambda: env_str(
            "CSP_FRAME_ANCESTORS_LAN",
            "http://192.168.1.10:8900 http://192.168.1.55:8900",
        )
    )

    # ── CORS ──
    cors_origin_regex: str = field(
        default_factory=lambda: os.environ.get(
            "CORS_ORIGIN_REGEX",
            r"^https?://"
            r"(localhost|127\.0\.0\.1"
            r"|192\.168\.1\.(25[0-5]|2[0-4]\d|[01]?\d\d?))"
            r"(:\d+)?$"
            r"|^https://[\w-]+\.techvisioncloud\.pl$",
        )
    )

    # ── Thermal monitor ──
    thermal_zone_path: Path = field(
        default_factory=lambda: _env_path("THERMAL_ZONE_PATH", "/sys/class/thermal/thermal_zone0/temp")
    )
    thermal_poll_sec: int = field(default_factory=lambda: _env_int("THERMAL_POLL_SEC", "10"))
    thermal_warn_c: float = field(default_factory=lambda: _env_float("THERMAL_WARN_C", "70"))
    thermal_crit_c: float = field(default_factory=lambda: _env_float("THERMAL_CRIT_C", "80"))
    thermal_resume_c: float = field(default_factory=lambda: _env_float("THERMAL_RESUME_C", "65"))
    fps_profile_path: Path = field(
        default_factory=lambda: _env_path("FPS_PROFILE_PATH", "/run/camera/fps_profile")
    )

    # ── FDIR event logging ──
    fdir_ring_max: int = field(default_factory=lambda: _env_int("FDIR_RING_MAX", "500"))
    fdir_log_dir: Path = field(default_factory=lambda: _env_path("FDIR_LOG_DIR", "/var/log/camera-fdir"))
    fdir_log_max_bytes: int = field(
        default_factory=lambda: _env_int("FDIR_LOG_MAX_BYTES", str(5 * 1024 * 1024))
    )

    # ── FDIR recovery ladder persistence ──
    fdir_ladder_state: Path = field(
        default_factory=lambda: _env_path("FDIR_LADDER_STATE", "/run/camera/fdir_ladder.json")
    )
    fdir_persist_dir: Path = field(
        default_factory=lambda: _env_path("FDIR_PERSIST_DIR", "/var/lib/camera-fdir")
    )
    fdir_dedup_sec: float = field(default_factory=lambda: _env_float("FDIR_DEDUP_SEC", "3"))

    # ── System mode ──
    mode_listener_timeout_sec: float = field(
        default_factory=lambda: _env_float("MODE_LISTENER_TIMEOUT_SEC", "5")
    )

    # ── Rate limiting (sliding-window, in-process) ──
    rate_limit_enabled: bool = field(default_factory=lambda: _env_bool("RATE_LIMIT_ENABLED", "1"))
    rate_limit_window_sec: float = field(default_factory=lambda: _env_float("RATE_LIMIT_WINDOW_SEC", "10"))
    rate_limit_snapshot: int = field(default_factory=lambda: _env_int("RATE_LIMIT_SNAPSHOT", "10"))
    rate_limit_healthz: int = field(default_factory=lambda: _env_int("RATE_LIMIT_HEALTHZ", "30"))
    rate_limit_janus_ws: int = field(default_factory=lambda: _env_int("RATE_LIMIT_JANUS_WS", "5"))
    rate_limit_max_buckets: int = field(
        default_factory=lambda: _env_int("RATE_LIMIT_MAX_BUCKETS", "10000")
    )

    # ── Trusted proxies (comma-separated IPs) for X-Forwarded-For ──
    trusted_proxies: str = field(default_factory=lambda: env_str("TRUSTED_PROXIES", "127.0.0.1"))

    # ── janus.js integrity ──
    janus_js_sha256: str = field(default_factory=lambda: env_str("JANUS_JS_SHA256", ""))

    # ── Startup behaviour ──
    # When True, critical startup checks (missing janus.js, missing TURN
    # credentials) abort the service instead of logging and continuing.
    # Default on so production deployments fail fast; set to "0" in dev/test.
    startup_fail_fast: bool = field(default_factory=lambda: _env_bool("CAM_STARTUP_FAIL_FAST", "1"))

    def __post_init__(self) -> None:
        import logging
        _log = logging.getLogger(__name__)
        errors: list[str] = []

        # ── Camera type ──
        if self.camera_type not in ("color_camera", "depth_camera"):
            errors.append(
                f"CAM_TYPE must be 'color_camera' or 'depth_camera', got '{self.camera_type}'"
            )

        # ── Port ranges ──
        for name, val in [("TURN_PORT", self.turn_port), ("TURN_TLS_PORT", self.turn_tls_port)]:
            if not 1 <= val <= 65535:
                errors.append(f"{name} must be 1–65535, got {val}")

        # ── Temperature thresholds ──
        if not (self.thermal_resume_c < self.thermal_warn_c < self.thermal_crit_c):
            errors.append(
                f"Temperature thresholds must satisfy THERMAL_RESUME_C ({self.thermal_resume_c}) "
                f"< THERMAL_WARN_C ({self.thermal_warn_c}) < THERMAL_CRIT_C ({self.thermal_crit_c})"
            )

        # ── Positive integers ──
        for name, val in [
            ("CAM_WATCHDOG_INTERVAL", self.watchdog_interval_sec),
            ("CAM_WATCHDOG_STALE_MS", self.watchdog_stale_ms),
            ("WATCHDOG_NOMINAL_CHECKS", self.watchdog_nominal_checks),
            ("FDIR_RING_MAX", self.fdir_ring_max),
        ]:
            if val <= 0:
                errors.append(f"{name} must be > 0, got {val}")

        # ── Non-negative integers ──
        if self.max_fdir_reboots < 0:
            errors.append(f"MAX_FDIR_REBOOTS must be >= 0, got {self.max_fdir_reboots}")

        # ── Positive floats ──
        for name, val in [
            ("JANUS_TIMEOUT", self.janus_timeout),
            ("THERMAL_POLL_SEC", self.thermal_poll_sec),
        ]:
            if val <= 0:
                errors.append(f"{name} must be > 0, got {val}")

        # ── ICE policy ──
        if self.ice_policy not in ("all", "relay"):
            errors.append(f"ICE_POLICY must be 'all' or 'relay', got '{self.ice_policy}'")

        # ── CORS regex ──
        if self.cors_origin_regex:
            import re
            try:
                re.compile(self.cors_origin_regex)
            except re.error as exc:
                errors.append(f"CORS_ORIGIN_REGEX is not valid regex: {exc}")

        # ── TURN credentials ──
        if not self.turn_pass and not self.turn_shared_secret:
            _log.warning(
                "Neither TURN_PASS nor TURN_SHARED_SECRET is set — "
                "TURN authentication will fail for remote clients. "
                "Set at least one via environment or /etc/robot/camera-secrets.env"
            )

        if errors:
            msg = "Settings validation failed:\n  - " + "\n  - ".join(errors)
            raise ValueError(msg)


@lru_cache
def get_settings() -> Settings:
    return Settings()
