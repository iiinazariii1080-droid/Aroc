"""Tests for routes/robot.py — proxy endpoints using Nav2AdapterClient."""

import pytest
from unittest.mock import AsyncMock, patch
from exceptions import DeviceConnectionError, DeviceError


# ── Symovo teleop config GET ────────────────────────────────────

def test_teleop_config_get_success(client):
    """GET /symovo_teleop_config returns config from nav2adapter."""
    with patch("routes.robot.robot.agv.teleop_config_get", new_callable=AsyncMock,
               return_value={"duration": 0.25, "linear_m_s": 0.1, "angular_rad_s": 0.5}):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["duration"] == 0.25
        assert data["linear_m_s"] == 0.1


def test_teleop_config_get_backend_error(client):
    """Backend error → 503."""
    with patch("routes.robot.robot.agv.teleop_config_get", new_callable=AsyncMock,
               side_effect=DeviceError("nav2adapter error")):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 503


def test_teleop_config_get_connection_error(client):
    """Connection error → 503."""
    with patch("routes.robot.robot.agv.teleop_config_get", new_callable=AsyncMock,
               side_effect=DeviceConnectionError("unreachable")):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 503


# ── Symovo teleop config PUT ────────────────────────────────────

def test_teleop_config_put_success(client):
    """PUT /symovo_teleop_config updates config."""
    with patch("routes.robot.robot.agv.teleop_config_put", new_callable=AsyncMock,
               return_value={"duration": 0.3, "linear_m_s": 0.2, "angular_rad_s": 0.6}):
        resp = client.put("/symovo_teleop_config", json={"duration": 0.3})
        assert resp.status_code == 200
        assert resp.json()["duration"] == 0.3


def test_teleop_config_put_backend_error(client):
    """Backend error → 503."""
    with patch("routes.robot.robot.agv.teleop_config_put", new_callable=AsyncMock,
               side_effect=DeviceError("bad request")):
        resp = client.put("/symovo_teleop_config", json={"duration": 0.3})
        assert resp.status_code == 503


# ── Symovo drive mode ───────────────────────────────────────────

def test_drive_mode_enable_success(client):
    """PUT /symovo_drive_mode enable=True → success."""
    with patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
               return_value={"enabled": True}):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


def test_drive_mode_backend_error(client):
    """Backend DeviceError → success=False (non-fatal)."""
    with patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
               side_effect=DeviceError("already enabled")):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 200
        assert resp.json()["success"] is False


def test_drive_mode_connection_error(client):
    """Connection error → 502."""
    with patch("routes.robot.robot.agv.drive_mode", new_callable=AsyncMock,
               side_effect=DeviceConnectionError("no connection")):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 502


# ── Tasked endpoints (move_to_box, transport, etc) ──────────────

def test_move_to_box_1_success(client):
    with patch("routes.robot.robot.move_robot_to_box_1", new_callable=AsyncMock, return_value=True):
        resp = client.post("/move/to_box_1", json={"velocity_percent": 40})
        assert resp.status_code == 200


def test_move_to_box_2_success(client):
    with patch("routes.robot.robot.move_robot_to_box_2", new_callable=AsyncMock, return_value=True):
        resp = client.post("/move/to_box_2", json={"velocity_percent": 40})
        assert resp.status_code == 200


def test_transport_position_success(client):
    with patch("routes.robot.robot.move_to_transport_position", new_callable=AsyncMock, return_value=True):
        resp = client.post("/move/to_transport_position", json={"velocity_percent": 40})
        assert resp.status_code == 200


def test_set_robot_ready_success(client):
    with patch("routes.robot.robot.set_ready", new_callable=AsyncMock, return_value=True):
        resp = client.post("/set_ready")
        assert resp.status_code == 200


def test_autotake_success(client):
    with patch("routes.robot.robot.autotake", new_callable=AsyncMock, return_value=True):
        resp = client.post("/move/autotake", json={"velocity_percent": 40})
        assert resp.status_code == 200


def test_move_to_product_success(client):
    with patch("routes.robot.robot.move_robot_to_product", new_callable=AsyncMock, return_value=True):
        resp = client.post("/move/to_product", json={
            "product_id": "TEST_1",
            "location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 0, "map_id": 0},
            "lift_position_cm": 10.0,
            "manipulator_offsets": {"x_offset_mm": 0, "y_offset_mm": 0, "z_offset_mm": 0},
            "velocity_percent": 30,
            "reset_faults": False,
        })
        assert resp.status_code == 200
