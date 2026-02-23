"""Tests for robot_service HTTP route handlers.

Covers stateless/CRUD endpoints: status, robot_positions, trajectory, tasks.
The orchestration endpoints (move_to_product, move_to_box_*) depend heavily
on robot_scripts and are better suited for integration tests.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Status endpoint ────────────────────────────────────────────────────────

class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_returns_system_status(self, client):
        mock_result = {
            "ready": True,
            "message": "",
            "igus": {
                "status_word": 8192,
                "homed": True,
                "is_moving": False,
                "error": False,
                "connected": True,
                "position": 50.0,
            },
            "symovo": {"error": {"type": "offline", "msg": "not connected", "raw": {}}},
            "xarm": {"connected": True},
        }
        with patch("app.robot_scripts.get_robot_system_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = mock_result
            r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert data["ready"] is True


# ── Trajectory config endpoints ────────────────────────────────────────────

class TestTrajectoryConfig:
    def test_get_trajectory_returns_config(self, client):
        with patch("routes.robot.get_trajectory", return_value={"steps": [{"x": 1}], "speed": 50}):
            r = client.get("/autotake_config/trajectory")
        assert r.status_code == 200
        assert r.json()["speed"] == 50

    def test_get_trajectory_404_when_empty(self, client):
        """If no trajectory has been saved, returns 404."""
        with patch("routes.robot.get_trajectory", return_value=None):
            r = client.get("/autotake_config/trajectory")
        assert r.status_code == 404

    def test_save_trajectory(self, client):
        with patch("routes.robot.save_trajectory") as mock_save:
            r = client.post("/autotake_config/trajectory", json={"steps": [{"x": 1}]})
        assert r.status_code == 201
        mock_save.assert_called_once()


# ── Robot positions CRUD ───────────────────────────────────────────────────

class TestRobotPositionsCRUD:
    def test_list_positions(self, client):
        with patch("routes.robot.get_robot_positions_list", return_value=[{"id": "abc", "name": "pos1"}]):
            r = client.get("/robot_positions/list")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_save_position(self, client):
        with patch("routes.robot.save_robot_position") as mock_save:
            payload = {
                "name": "test_pos",
                "params": {
                    "location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0, "map_id": 0},
                    "lift_position_cm": 10.0,
                    "velocity_percent": 50.0,
                },
            }
            r = client.post("/robot_positions/save", json=payload)
        assert r.status_code == 201
        assert r.json()["id"]  # non-empty generated id
        mock_save.assert_called_once()

    def test_save_minimal_params(self, client):
        """Saving with minimal params merges with defaults."""
        with patch("routes.robot.save_robot_position"):
            r = client.post("/robot_positions/save", json={"name": "minimal"})
        assert r.status_code == 201

    def test_delete_position(self, client):
        with patch("routes.robot.delete_robot_position") as mock_del:
            r = client.post("/robot_positions/delete", params={"position_id": "abc123"})
        assert r.status_code == 201
        mock_del.assert_called_once_with("abc123")


# ── Task management endpoints ─────────────────────────────────────────────

class TestTaskEndpoints:
    def test_current_task_when_idle(self, client):
        r = client.get("/tasks/current")
        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] is None

    def test_task_status_not_found(self, client):
        r = client.get(f"/tasks/status/{uuid.uuid4().hex}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "not_found"

    def test_cancel_current_when_idle(self, client):
        r = client.post("/tasks/cancel_current")
        assert r.status_code == 200


# ── Joystick HTTP endpoint ────────────────────────────────────────────────

class TestJoystickFrame:
    def test_valid_frame_accepted(self, client):
        frame = {
            "ts": 1000.0,
            "axes": [0.0, 0.0, 0.0, 0.0],
            "buttons": [0] * 19,
            "ttl": 150,
        }
        r = client.post("/joystick/frame", json=frame)
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_invalid_axes_rejected(self, client):
        frame = {
            "ts": 1000.0,
            "axes": [0.0, 0.0],  # too few
            "buttons": [0] * 19,
            "ttl": 150,
        }
        r = client.post("/joystick/frame", json=frame)
        # Either 422 from pydantic or 200 with success=False depending on validation layer
        if r.status_code == 200:
            assert r.json()["success"] is False


# ── Helper function unit tests ─────────────────────────────────────────────

class TestHelperFunctions:
    def test_deep_merge(self):
        from routes.robot import _deep_merge

        base = {"a": 1, "b": {"c": 2, "d": 3}}
        update = {"b": {"c": 99}, "e": 5}
        result = _deep_merge(base, update)
        assert result == {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}

    def test_deep_merge_none_updates(self):
        from routes.robot import _deep_merge

        base = {"a": 1}
        result = _deep_merge(base, None)
        assert result == {"a": 1}

    def test_to_obj(self):
        from routes.robot import _to_obj

        params = {
            "location": {"x_m": 1.5, "y_m": 2.5, "theta_deg": 90.0, "map_id": 1},
            "lift_position_cm": 15.0,
            "manipulator_offsets": {"x_offset_mm": 10.0, "y_offset_mm": 20.0, "z_offset_mm": 30.0},
            "velocity_percent": 75.0,
            "reset_faults": True,
        }
        obj = _to_obj(params)
        assert obj.location.x_m == 1.5
        assert obj.lift_position_cm == 15.0
        assert obj.manipulator_offsets.z_offset_mm == 30.0
        assert obj.velocity_percent == 75.0
        assert obj.reset_faults is True

    def test_apply_deadzone(self):
        from routes.robot import _apply_deadzone

        assert _apply_deadzone(0.01, dz=0.05) == 0.0
        assert _apply_deadzone(0.1, dz=0.05) == 0.1

    def test_clamp_ttl(self):
        from routes.robot import _clamp_ttl

        assert _clamp_ttl(None) > 0  # uses default
        assert _clamp_ttl(10.0) == 50  # clamped to min
        assert _clamp_ttl(9999.0) == 500  # clamped to max
        assert _clamp_ttl(200.0) == 200
