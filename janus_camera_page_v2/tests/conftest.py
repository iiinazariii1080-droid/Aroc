"""Shared fixtures for janus_camera_page tests."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

# fcntl is Linux-only; stub it so tests run on Windows (CI / dev machines).
if sys.platform == "win32" and "fcntl" not in sys.modules:
    sys.modules["fcntl"] = MagicMock()

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset all module-level singletons before each test.

    Uses ServiceRegistry.reset() to centralize cleanup instead of
    manually resetting 8+ module-level variables.
    """
    from app.core.registry import ServiceRegistry
    ServiceRegistry.reset()

    yield

    ServiceRegistry.teardown()


@pytest.fixture
def settings():
    """Return test settings instance."""
    from app.core.settings import Settings
    return Settings()


def make_test_settings(tmp_path, **overrides):
    """Create a real Settings instance with test-safe defaults.

    Unlike MagicMock(), accessing a non-existent attribute raises
    AttributeError — catching typos in test code immediately.

    All filesystem paths point into tmp_path so tests never touch
    production paths. Override any field via keyword arguments.
    """
    from app.core.settings import Settings

    defaults = dict(
        app_title="cam-control-test",
        app_version="0.0.0-test",
        base_dir=tmp_path,
        templates_dir=tmp_path / "templates",
        static_dir=tmp_path / "static",
        env_path=tmp_path / "cam-rgb.env",
        lock_path=tmp_path / "cam-rgb.env.lock",
        camera_device="/dev/null",
        camera_type="color_camera",
        service_name="test.service",
        janus_service_name="janus-test.service",
        realsense_failsafe_service_name="realsense-failsafe-test.service",
        api_key=None,
        admin_token="test-token",
        admin_enforce=False,
        snapshot_path=str(tmp_path / "snapshot.jpg"),
        janus_url="http://127.0.0.1:8088/janus",
        janus_timeout=1.0,
        janus_mount_id=1305,
        janus_http_base="http://127.0.0.1:8088",
        janus_cfg_path=tmp_path / "janus.jcfg",
        janus_nat_json=tmp_path / "janus-nat.json",
        nat_config_ttl_sec=0.0,
        depth_cam_janus_ws_path="/janus-ws",
        janus_ws_backends={"1": "ws://127.0.0.1:8188/janus-ws"},
        relay_url="http://127.0.0.1:9000",
        depth_cam_url="http://127.0.0.1:8900",
        realsense_mux_url="http://127.0.0.1:8000",
        allow_insecure_tls=False,
        turn_host="127.0.0.1",
        turn_port=3478,
        turn_tls_port=443,
        turn_user="webrtc",
        turn_pass="test-pass",
        turn_shared_secret="",
        turn_cred_ttl=86400,
        ice_policy="all",
        watchdog_enabled=False,
        snapshot_watchdog_enabled=False,
        watchdog_interval_sec=1,
        watchdog_stale_ms=5000,
        watchdog_grace_sec=0,
        watchdog_nominal_checks=3,
        watchdog_reboot_enabled=True,
        max_fdir_reboots=2,
        csp_frame_ancestors_lan="http://127.0.0.1:8900",
        cors_origin_regex=r"^https?://localhost(:\d+)?$",
        thermal_zone_path=tmp_path / "thermal_zone0" / "temp",
        thermal_poll_sec=1,
        thermal_warn_c=70.0,
        thermal_crit_c=80.0,
        thermal_resume_c=65.0,
        fps_profile_path=tmp_path / "fps_profile",
        fdir_ring_max=100,
        fdir_log_dir=tmp_path / "fdir-logs",
        fdir_log_max_bytes=1024 * 1024,
        fdir_ladder_state=tmp_path / "fdir_ladder.json",
        fdir_persist_dir=tmp_path / "camera-fdir",
        fdir_dedup_sec=0.0,
        mode_listener_timeout_sec=2.0,
        rate_limit_enabled=False,
        rate_limit_window_sec=10.0,
        rate_limit_snapshot=100,
        rate_limit_healthz=100,
        rate_limit_janus_ws=100,
        rate_limit_max_buckets=1000,
        trusted_proxies="127.0.0.1",
        janus_js_sha256="",
        startup_fail_fast=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def app(tmp_path):
    """Create a test-safe app instance with mocked event handlers."""
    _no_admin = make_test_settings(tmp_path, admin_enforce=False)
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.admin.get_settings", return_value=_no_admin):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
