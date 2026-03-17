"""Tests for routes/robot.py — helper functions + endpoints."""

import time
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from fastapi.testclient import TestClient


# ── import helpers directly ──────────────────────────────────────
from routes.robot import (
    _JoystickFrameValidator,
    _apply_deadzone,
    _clamp_ttl,
    _prepare_frame_payload,
    _enqueue_frame,
    _deep_merge,
    _to_obj,
    DEFAULT_POSITION_PARAMS,
)


# ── _JoystickFrameValidator ─────────────────────────────────────
class TestJoystickFrameValidator:
    def setup_method(self):
        self.v = _JoystickFrameValidator()

    def test_valid_frame(self):
        payload = {
            "ts": 1.0,
            "axes": [0.0, 0.1, -0.2, 0.3],
            "buttons": [0] * 19,
            "ttl": 100,
        }
        result = self.v.validate(payload)
        assert result["ts"] == 1.0
        assert len(result["axes"]) == 4
        assert len(result["buttons"]) == 19
        assert result["ttl"] == 100

    def test_default_ts(self):
        payload = {"axes": [0] * 4, "buttons": [0] * 19}
        result = self.v.validate(payload)
        assert result["ts"] > 0

    def test_bad_axes_length(self):
        with pytest.raises(ValueError, match="axes"):
            self.v.validate({"axes": [0, 1], "buttons": [0] * 19})

    def test_bad_buttons_length(self):
        with pytest.raises(ValueError, match="buttons"):
            self.v.validate({"axes": [0] * 4, "buttons": [0, 1]})


# ── _apply_deadzone ─────────────────────────────────────────────
def test_deadzone_above():
    assert _apply_deadzone(0.5, 0.1) == 0.5

def test_deadzone_below():
    assert _apply_deadzone(0.05, 0.1) == 0.0

def test_deadzone_negative():
    assert _apply_deadzone(-0.5, 0.1) == -0.5


# ── _clamp_ttl ──────────────────────────────────────────────────
def test_clamp_ttl_none():
    result = _clamp_ttl(None)
    assert 50 <= result <= 500

def test_clamp_ttl_low():
    assert _clamp_ttl(10) == 50

def test_clamp_ttl_high():
    assert _clamp_ttl(9999) == 500

def test_clamp_ttl_normal():
    assert _clamp_ttl(200) == 200


# ── _prepare_frame_payload ──────────────────────────────────────
def test_prepare_frame_valid():
    payload = {"ts": 1.0, "axes": [0] * 4, "buttons": [0] * 19, "ttl": 150}
    data, err = _prepare_frame_payload(payload)
    assert data is not None
    assert err is None

def test_prepare_frame_invalid():
    payload = {"axes": [0, 1], "buttons": [0] * 19}
    data, err = _prepare_frame_payload(payload)
    assert data is None
    assert "validation_error" in err


# ── _enqueue_frame ──────────────────────────────────────────────
def test_enqueue_frame_via_ingress():
    ingress = MagicMock()
    ingress.submit = MagicMock(return_value=(True, None))
    state = SimpleNamespace(joystick_ingress=ingress)
    ok, err = _enqueue_frame(state, {"ts": 1}, source="http")
    assert ok is True

def test_enqueue_frame_via_scheduler():
    scheduler = MagicMock()
    scheduler.submit = MagicMock(return_value=(True, None))
    state = SimpleNamespace(joystick_ingress=None, joystick_scheduler=scheduler)
    ok, err = _enqueue_frame(state, {"ts": 1}, source="http")
    assert ok is True

def test_enqueue_frame_via_pipeline():
    jp = MagicMock()
    jp.submit = MagicMock(return_value=True)
    state = SimpleNamespace(joystick_ingress=None, joystick_scheduler=None, joystick_pipeline=jp)
    ok, err = _enqueue_frame(state, {"ts": 1}, source="http")
    assert ok is True

def test_enqueue_frame_no_pipeline():
    state = SimpleNamespace(joystick_ingress=None, joystick_scheduler=None, joystick_pipeline=None)
    ok, err = _enqueue_frame(state, {"ts": 1}, source="http")
    assert ok is False
    assert "unavailable" in err


# ── _deep_merge ─────────────────────────────────────────────────
def test_deep_merge_flat():
    assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

def test_deep_merge_nested():
    result = _deep_merge(
        {"loc": {"x": 0, "y": 0}},
        {"loc": {"x": 5}},
    )
    assert result == {"loc": {"x": 5, "y": 0}}

def test_deep_merge_none_updates():
    assert _deep_merge({"a": 1}, None) == {"a": 1}


# ── _to_obj ─────────────────────────────────────────────────────
def test_to_obj_defaults():
    obj = _to_obj(DEFAULT_POSITION_PARAMS)
    assert obj.location.x_m == 0.0
    assert obj.velocity_percent == 0.0
    assert obj.reset_faults is False

def test_to_obj_with_values():
    params = {
        "location": {"x_m": 1.5, "y_m": 2.5, "theta_deg": 90, "map_id": 1},
        "lift_position_cm": 10.0,
        "manipulator_offsets": {"x_offset_mm": 5, "y_offset_mm": 6, "z_offset_mm": 7},
        "velocity_percent": 40,
        "reset_faults": True,
        "product_id": "abc",
    }
    obj = _to_obj(params)
    assert obj.location.x_m == 1.5
    assert obj.manipulator_offsets.x_offset_mm == 5
    assert obj.velocity_percent == 40
    assert obj.product_id == "abc"

def test_to_obj_no_manipulator_offsets():
    params = dict(DEFAULT_POSITION_PARAMS, manipulator_offsets=None)
    obj = _to_obj(params)
    # When mo is None the code still creates a zero-offset namespace (due to `or {}`)
    assert obj.manipulator_offsets is not None
    assert obj.manipulator_offsets.x_offset_mm == 0.0


# ── endpoint tests (via TestClient) ─────────────────────────────
@pytest.fixture
def noop_startup():
    from main import app
    _orig = app.router.on_startup.copy()
    _orig_shutdown = app.router.on_shutdown.copy()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    # Provide minimal app.state
    app.state.joystick_pipeline = None
    app.state.joystick_ingress = None
    app.state.joystick_scheduler = None

    yield app

    app.router.on_startup[:] = _orig
    app.router.on_shutdown[:] = _orig_shutdown


@pytest.fixture
def client(noop_startup):
    return TestClient(noop_startup, raise_server_exceptions=False)


def test_health_details(client):
    resp = client.get("/health/details")
    assert resp.status_code == 200
    data = resp.json()
    assert "joystick_worker_running" in data
    assert data["joystick_worker_running"] is False


def test_check_devices_ready(client):
    from models.api_types import ErrorStatus
    with patch("routes.robot.robot.get_robot_system_status", new_callable=AsyncMock) as mock_status:
        mock_status.return_value = {
            "ready": True,
            "message": "",
            "igus": ErrorStatus(error=False),
            "symovo": ErrorStatus(error=False),
            "xarm": {"connected": True},
        }
        resp = client.get("/status")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True


def test_joystick_frame_http(client):
    payload = {
        "ts": time.time(),
        "axes": [0.0] * 4,
        "buttons": [0] * 19,
        "ttl": 150,
    }
    resp = client.post("/joystick/frame", json=payload)
    # Pipeline unavailable → still returns 200 with error message
    assert resp.status_code == 200


def test_get_trajectory(client):
    with patch("routes.robot.get_trajectory", return_value={"steps": [1, 2, 3]}):
        resp = client.get("/autotake_config/trajectory")
        assert resp.status_code == 200
        assert resp.json() == {"steps": [1, 2, 3]}


def test_get_trajectory_not_found(client):
    with patch("routes.robot.get_trajectory", return_value=None):
        resp = client.get("/autotake_config/trajectory")
        assert resp.status_code == 404


def test_save_trajectory(client):
    with patch("routes.robot.save_trajectory") as mock_save:
        resp = client.post("/autotake_config/trajectory", json={"steps": [1]})
        assert resp.status_code == 201
        mock_save.assert_called_once()


def test_get_robot_positions_list(client):
    with patch("routes.robot.get_robot_positions_list", return_value=[]):
        resp = client.get("/robot_positions/list")
        assert resp.status_code == 200


def test_save_robot_position(client):
    with patch("routes.robot.save_robot_position"):
        resp = client.post(
            "/robot_positions/save",
            json={"name": "test", "params": {"velocity_percent": 20}},
        )
        assert resp.status_code == 201
        assert "id" in resp.json()


def test_delete_robot_position(client):
    with patch("routes.robot.delete_robot_position"):
        resp = client.post("/robot_positions/delete?position_id=abc")
        assert resp.status_code == 201


def test_task_status_unknown(client):
    resp = client.get("/tasks/status/nonexistent")
    assert resp.status_code == 200


def test_current_task_none(client):
    resp = client.get("/tasks/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] is None


def test_cancel_current_no_task(client):
    resp = client.post("/tasks/cancel_current")
    assert resp.status_code == 200


def test_symovo_drive_mode_no_url(client):
    with patch("routes.robot.SYMOVO_DRIVE_MODE_URL", ""):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 503


def test_symovo_teleop_config_no_url(client):
    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", ""):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 503


# ── /tasks/navigate ────────────────────────────────────────────
# Note: @tasked_getter wraps the handler as a background task,
# so the endpoint always returns 200 with a task_id.  Errors
# (unknown target, invalid params) appear in task status, not HTTP status.

def test_tasks_navigate_returns_task_id(client):
    """Endpoint accepts NavigateRequest and returns 200 with task_id."""
    position = {
        "id": "pos1",
        "name": "station_A",
        "params": {
            "location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90, "map_id": 0},
            "lift_position_cm": 10.0,
            "velocity_percent": 30.0,
        },
    }
    with patch("routes.robot.get_robot_position_by_name", return_value=position), \
         patch("routes.robot.robot.move_robot_to_product", new_callable=AsyncMock, return_value=True):
        resp = client.post(
            "/tasks/navigate",
            json={"command_id": "nav-1", "target_id": "station_A"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "task_id" in data


def test_tasks_navigate_unknown_target_returns_task(client):
    """Unknown target_id still returns 200 with task_id (error in task status)."""
    with patch("routes.robot.get_robot_position_by_name", return_value=None):
        resp = client.post(
            "/tasks/navigate",
            json={"command_id": "nav-2", "target_id": "nonexistent"},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()


def test_tasks_navigate_validation_error(client):
    """Missing required field command_id returns 422 (pydantic validation)."""
    resp = client.post(
        "/tasks/navigate",
        json={"target_id": "station_A"},
    )
    assert resp.status_code == 422
