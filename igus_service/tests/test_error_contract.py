"""IT-02: ServiceError → HTTP Error Code Contract tests.

Verifies that driver exceptions map to the correct HTTP status code AND
error code in the response body. Tests the real FastAPI middleware +
ServiceError translation chain — not just status codes.

Boundary: A+B (HTTP ↔ Application ↔ Driver)
Risk: R-04 — wiring tests check status codes but not error semantics
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import main
from tests.fakes import FakeDrive, FakeEventBus, AsyncNoopLock, ControllableLock, set_app_state


_MOVE_BODY = {
    "target_position": 1000,
    "relative": False,
    "profile": {"velocity": 100, "acceleration": 50, "deceleration": 50},
}


class TestDriveInFault:
    """fault_mode=True → HTTP 409 + DRIVE_IN_FAULT code."""

    def test_move_in_fault_returns_409_with_code(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive(fault_mode=True), motor_lock=ControllableLock())
            r = c.post("/drive/move_to_position", json=_MOVE_BODY)
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "DRIVE_IN_FAULT"
        assert "fault" in body["message"].lower()

    def test_jog_in_fault_returns_409_with_code(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive(fault_mode=True), motor_lock=ControllableLock())
            r = c.post("/drive/jog_start", json={"direction": "positive", "speed": 10})
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "DRIVE_IN_FAULT"

    def test_reference_in_fault_returns_409_with_code(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive(fault_mode=True), motor_lock=ControllableLock())
            r = c.post("/drive/reference", json={})
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "DRIVE_IN_FAULT"


class TestDriveOffline:
    """drive=None → HTTP 503 + DRIVE_NOT_INITIALIZED code."""

    def test_move_offline_returns_503_with_code(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.post("/drive/move_to_position", json=_MOVE_BODY)
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "DRIVE_NOT_INITIALIZED"

    def test_status_offline_returns_503_with_code(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            r = c.get("/drive/status")
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "DRIVE_NOT_INITIALIZED"


class TestMotorBusy:
    """Locked motor_lock → HTTP 409 + MOTOR_BUSY code."""

    def test_jog_while_busy_returns_409_motor_busy(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(
                main.app,
                drive=FakeDrive(),
                motor_lock=ControllableLock(locked_state=True),
            )
            r = c.post("/drive/jog_start", json={"direction": "positive", "speed": 10})
        assert r.status_code == 409
        body = r.json()
        assert body["code"] == "MOTOR_BUSY"


class TestTimeout:
    """TimeoutError from driver → HTTP 504 + TIMEOUT code."""

    def test_move_timeout_returns_504_with_code(self, noop_lifecycle):
        drive = FakeDrive(raise_on_move=TimeoutError("target not reached"))
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drive, motor_lock=ControllableLock())
            r = c.post("/drive/move_to_position", json=_MOVE_BODY)
        assert r.status_code == 504
        body = r.json()
        assert body["code"] == "TIMEOUT"

    def test_home_timeout_returns_504_with_code(self, noop_lifecycle):
        drive = FakeDrive(raise_on_home=TimeoutError("homing timeout"))
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=drive, motor_lock=ControllableLock())
            r = c.post("/drive/reference", json={})
        assert r.status_code == 504
        body = r.json()
        assert body["code"] == "TIMEOUT"


class TestDisconnected:
    """Disconnected drive → HTTP 503 + appropriate code."""

    def test_disconnected_drive_returns_503(self, noop_lifecycle):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive(is_connected=False), motor_lock=ControllableLock())
            r = c.get("/drive/status")
        assert r.status_code == 503
        body = r.json()
        assert isinstance(body.get("code"), str)
