"""Tests for igus_service API v1 routes (api_routes.py) and system routes.

Uses FakeDrive from conftest to avoid hardware dependencies.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from tests.fakes import FakeDrive, FakeEventBus, AsyncNoopLock, set_app_state


@pytest.fixture
def client(noop_lifecycle):
    """TestClient with hardware patched out, state populated."""
    with TestClient(main.app) as c:
        set_app_state(
            main.app,
            drive=FakeDrive(is_connected=True),
            event_bus=FakeEventBus(),
            motor_lock=AsyncNoopLock(),
        )
        yield c


# ── Legacy routes (/move, /reference, /fault_reset, /position, /status) ────

class TestLegacyRoutes:
    def test_get_position(self, client):
        r = client.get("/position")
        assert r.status_code == 200
        data = r.json()
        assert data["position"] == 100

    def test_get_is_motion(self, client):
        r = client.get("/is_motion")
        assert r.status_code == 200
        data = r.json()
        assert data["is_moving"] is False

    def test_get_status(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data.get("status_word"), int) or isinstance(data.get("homed"), bool)

    def test_drive_not_initialized(self, client):
        """If drive is None, returns error status."""
        main.app.state.drive = None
        r = client.get("/position")
        assert r.status_code == 503

    def test_get_position_out_of_range_succeeds(self, client):
        """PositionResponse must not validate hardware position against soft limits.

        If the drive returns a position outside [0, 120000] (e.g. due to a
        hardware configuration mismatch), the endpoint must return 200 with the
        raw value — not 500 from a Pydantic bounds check on the response model.
        """
        main.app.state.drive.get_position = AsyncMock(return_value=200_000)
        r = client.get("/position")
        assert r.status_code == 200
        assert r.json()["position"] == 200_000


# ── API v1 routes (/drive/status, /drive/jog_stop, etc.) ──────────────────

class TestApiV1DriveStatus:
    def test_drive_status_ok(self, client):
        r = client.get("/drive/status")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["data"], dict)

    def test_drive_telemetry(self, client):
        r = client.get("/drive/telemetry")
        assert r.status_code == 200


class TestApiV1DriveCommands:
    def test_jog_stop(self, client):
        r = client.post("/drive/jog_stop", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_stop(self, client):
        r = client.post("/drive/stop", json={})
        assert r.status_code == 200

    def test_fault_reset_api(self, client):
        r = client.post("/drive/fault_reset", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True


# ── System routes (/health, /ready, /info) ─────────────────────────────────

class TestSystemRoutes:
    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("ok", "healthy", "degraded", "unhealthy")

    def test_info_endpoint(self, client):
        r = client.get("/info")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data.get("version"), str) or isinstance(data.get("server_version"), str)

    def test_ready_endpoint(self, client):
        r = client.get("/ready")
        # Should return 200 or 503 depending on drive state
        assert r.status_code == 200

    def test_root_returns_page(self, client):
        r = client.get("/")
        # Should return HTML or redirect
        assert r.status_code == 200
