"""Integration tests for API v1 /drive/* endpoints.

Uses TestClient with noop_lifecycle + FakeDrive from conftest to avoid hardware
dependencies. Tests verify HTTP status codes, response shapes (ApiEnvelope),
and key business logic paths (MOTOR_BUSY, MotionAborted, offline drive).
"""

from __future__ import annotations

import main
import pytest
from fastapi.testclient import TestClient
from tests.fakes import ControllableLock, FakeDrive, FakeEventBus, set_app_state

from drivers.dryve_d1.protocol.exceptions import MotionAborted


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(noop_lifecycle) -> TestClient:
    """Default TestClient with connected drive and free motor lock."""
    with TestClient(main.app) as c:
        set_app_state(
            main.app,
            drive=FakeDrive(),
            event_bus=FakeEventBus(),
            motor_lock=ControllableLock(locked_state=False),
        )
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body["data"]


def _err(r, status: int) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert isinstance(body.get("code"), str), f"Expected error body with str 'code', got: {body}"
    return body


# ---------------------------------------------------------------------------
# GET /drive/status
# ---------------------------------------------------------------------------

class TestGetDriveStatus:
    def test_online_returns_200(self, client):
        data = _ok(client.get("/drive/status"))
        assert data["cia402_state"] == "operation_enabled"
        assert isinstance(data["statusword"], int)
        assert data["fault"]["active"] is False

    def test_offline_drive_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.get("/drive/status")
            err = _err(r, 503)
            assert "DRIVE_NOT_INITIALIZED" in err["code"]

    def test_disconnected_drive_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(is_connected=False),
                motor_lock=ControllableLock(),
            )
            r = c.get("/drive/status")
            assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /drive/telemetry
# ---------------------------------------------------------------------------

class TestGetDriveTelemetry:
    def test_returns_telemetry_fields(self, client):
        data = _ok(client.get("/drive/telemetry"))
        assert isinstance(data["ts"], int) and data["ts"] > 0
        assert isinstance(data["statusword"], int)
        assert data["position"] == 100  # FakeSnapshot default
        assert data["velocity"] == 0    # FakeSnapshot default

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.get("/drive/telemetry")
            _err(r, 503)


# ---------------------------------------------------------------------------
# GET /drive/trace/latest
# ---------------------------------------------------------------------------

class TestGetLatestTrace:
    def test_no_trace_returns_has_trace_false(self, client):
        main.app.state.latest_command_trace = None
        data = _ok(client.get("/drive/trace/latest"))
        assert data["has_trace"] is False
        assert data["trace"] is None

    def test_with_trace_returns_has_trace_true(self, client):
        import time as _time
        main.app.state.latest_command_trace = {
            "ts": int(_time.time() * 1000),
            "operation": "jog_start",
            "request_id": "req-1",
            "command_id": "cmd-abc",
            "op_id": "op-123",
        }
        data = _ok(client.get("/drive/trace/latest"))
        assert data["has_trace"] is True
        assert data["trace"]["operation"] == "jog_start"


# ---------------------------------------------------------------------------
# POST /drive/move_to_position
# ---------------------------------------------------------------------------

_MOVE_BODY = {
    "target_position": 1000.0,
    "relative": False,
    "profile": {"velocity": 500.0, "acceleration": 200.0, "deceleration": 200.0},
}


class TestMoveToPosition:
    def test_success_absolute(self, client):
        data = _ok(client.post("/drive/move_to_position", json=_MOVE_BODY))
        assert data["target_position"] == 1000.0
        assert data["aborted"] is False

    def test_aborted_returns_aborted_true(self, noop_lifecycle):
        drv = FakeDrive(raise_on_move=MotionAborted("stopped"))
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            data = _ok(c.post("/drive/move_to_position", json=_MOVE_BODY))
        assert data["aborted"] is True

    def test_drive_in_fault_returns_409(self, noop_lifecycle):
        drv = FakeDrive(fault_mode=True)
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            r = c.post("/drive/move_to_position", json=_MOVE_BODY)
        err = _err(r, 409)
        assert "DRIVE_IN_FAULT" in err["code"]

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/move_to_position", json=_MOVE_BODY)
        _err(r, 503)


# ---------------------------------------------------------------------------
# POST /drive/jog_start
# ---------------------------------------------------------------------------

_JOG_BODY = {"direction": "positive", "speed": 1000.0, "ttl_ms": 200}


class TestJogStart:
    def test_success_positive(self, client):
        data = _ok(client.post("/drive/jog_start", json=_JOG_BODY))
        assert data["direction"] == "positive"
        assert data["velocity"] > 0

    def test_success_negative(self, client):
        data = _ok(
            client.post("/drive/jog_start", json={**_JOG_BODY, "direction": "negative"})
        )
        assert data["direction"] == "negative"
        assert data["velocity"] < 0

    def test_motor_busy_returns_409(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(),
                motor_lock=ControllableLock(locked_state=True),
                event_bus=FakeEventBus(),
            )
            r = c.post("/drive/jog_start", json=_JOG_BODY)
        err = _err(r, 409)
        assert "MOTOR_BUSY" in err["code"]

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/jog_start", json=_JOG_BODY)
        _err(r, 503)


# ---------------------------------------------------------------------------
# POST /drive/jog_stop
# ---------------------------------------------------------------------------

class TestJogStop:
    def test_success(self, client):
        data = _ok(client.post("/drive/jog_stop", json={}))
        assert data["stopped"] is True

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/jog_stop", json={})
        _err(r, 503)


# ---------------------------------------------------------------------------
# POST /drive/stop
# ---------------------------------------------------------------------------

class TestStop:
    def test_quick_stop(self, client):
        data = _ok(client.post("/drive/stop", json={"mode": "quick_stop"}))
        assert data["mode"] == "quick_stop"

    def test_halt(self, client):
        data = _ok(client.post("/drive/stop", json={"mode": "halt"}))
        assert data["mode"] == "halt"

    def test_default_mode_is_quick_stop(self, client):
        data = _ok(client.post("/drive/stop", json={}))
        assert data["mode"] == "quick_stop"


# ---------------------------------------------------------------------------
# POST /drive/reference
# ---------------------------------------------------------------------------

class TestReference:
    def test_success(self, client):
        data = _ok(client.post("/drive/reference", json={}))
        assert data["homed"] is True
        assert data["aborted"] is False

    def test_aborted_returns_aborted_true(self, noop_lifecycle):
        drv = FakeDrive(raise_on_home=MotionAborted("stopped"))
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            data = _ok(c.post("/drive/reference", json={}))
        assert data["homed"] is False
        assert data["aborted"] is True

    def test_drive_in_fault_returns_409(self, noop_lifecycle):
        drv = FakeDrive(fault_mode=True)
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            r = c.post("/drive/reference", json={})
        err = _err(r, 409)
        assert "DRIVE_IN_FAULT" in err["code"]

    def test_motor_busy_returns_409(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(),
                motor_lock=ControllableLock(locked_state=True),
                event_bus=FakeEventBus(),
            )
            r = c.post("/drive/reference", json={})
        err = _err(r, 409)
        assert "MOTOR_BUSY" in err["code"]


# ---------------------------------------------------------------------------
# POST /drive/jog_update
# ---------------------------------------------------------------------------

_JOG_UPDATE_BODY = {"direction": "positive", "speed": 1000.0, "ttl_ms": 200}


class TestJogUpdate:
    def test_success(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(),
                motor_lock=ControllableLock(locked_state=False),
                event_bus=FakeEventBus(),
            )
            data = _ok(c.post("/drive/jog_update", json=_JOG_UPDATE_BODY))
        assert data["direction"] == "positive"
        assert data["velocity"] > 0

    def test_jog_update_succeeds_when_motor_busy(self, noop_lifecycle):
        """jog_update does not use motor_lock — succeeds even when lock held."""
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(),
                motor_lock=ControllableLock(locked_state=True),
                event_bus=FakeEventBus(),
            )
            data = _ok(c.post("/drive/jog_update", json=_JOG_UPDATE_BODY))
        assert data["direction"] == "positive"

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/jog_update", json=_JOG_UPDATE_BODY)
        _err(r, 503)


# ---------------------------------------------------------------------------
# POST /drive/fault_reset
# ---------------------------------------------------------------------------

class TestFaultReset:
    def test_success_auto_enable(self, noop_lifecycle):
        drv = FakeDrive()
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            data = _ok(c.post("/drive/fault_reset", json={"auto_enable": True}))
        assert data["auto_enable_requested"] is True
        assert data["fault_cleared"] is True

    def test_success_no_auto_enable(self, noop_lifecycle):
        drv = FakeDrive()
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            data = _ok(c.post("/drive/fault_reset", json={"auto_enable": False}))
        assert data["auto_enable_requested"] is False

    def test_drive_offline_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/fault_reset", json={})
        _err(r, 503)

    def test_response_contains_previous_fault(self, noop_lifecycle):
        drv = FakeDrive()
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drv, motor_lock=ControllableLock())
            data = _ok(c.post("/drive/fault_reset", json={}))
        assert data["previous_fault"] is None or isinstance(data["previous_fault"], dict)
        assert data["fault_cleared"] is True
