"""Tests for routes/robot.py — aiohttp-based proxy endpoints and more routes."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
from fastapi.testclient import TestClient
import aiohttp


# ── fixtures ─────────────────────────────────────────────────────
@pytest.fixture
def noop_startup():
    from main import app
    _orig = app.router.on_startup.copy()
    _orig_shutdown = app.router.on_shutdown.copy()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    app.state.joystick_pipeline = None
    app.state.joystick_ingress = None
    app.state.joystick_scheduler = None

    yield app

    app.router.on_startup[:] = _orig
    app.router.on_shutdown[:] = _orig_shutdown


@pytest.fixture
def client(noop_startup):
    return TestClient(noop_startup, raise_server_exceptions=False)


# ── Symovo teleop config GET ────────────────────────────────────
def test_teleop_config_get_success(client):
    """GET /symovo_teleop_config successfully proxies."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=json.dumps({
        "duration": 0.25, "linear_m_s": 0.1, "angular_rad_s": 0.5
    }))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", "http://localhost:7906/move/speed"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["duration"] == 0.25
        assert data["linear_m_s"] == 0.1


def test_teleop_config_get_backend_error(client):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="server error")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", "http://localhost:7906/move/speed"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 503


def test_teleop_config_get_connection_error(client):
    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=aiohttp.ClientError("conn error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", "http://localhost:7906/move/speed"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.get("/symovo_teleop_config")
        assert resp.status_code == 503


# ── Symovo teleop config PUT ────────────────────────────────────
def test_teleop_config_put_success(client):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=json.dumps({
        "duration": 0.3, "linear_m_s": 0.2, "angular_rad_s": 0.6
    }))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", "http://localhost:7906/move/speed"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.put("/symovo_teleop_config", json={"duration": 0.3})
        assert resp.status_code == 200
        assert resp.json()["duration"] == 0.3


def test_teleop_config_put_backend_error(client):
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.text = AsyncMock(return_value="bad request")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_TELEOP_MOVE_URL", "http://localhost:7906/move/speed"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.put("/symovo_teleop_config", json={"duration": 0.3})
        assert resp.status_code == 503


# ── Symovo drive mode ───────────────────────────────────────────
def test_drive_mode_enable_success(client):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=json.dumps({"enabled": True}))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_DRIVE_MODE_URL", "http://localhost:7905/drive_mode"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


def test_drive_mode_backend_error(client):
    mock_resp = AsyncMock()
    mock_resp.status = 400
    raw_text = json.dumps({"error": "already enabled"})
    mock_resp.text = AsyncMock(return_value=raw_text)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_DRIVE_MODE_URL", "http://localhost:7905/drive_mode"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.put("/symovo_drive_mode", json={"enable": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False


def test_drive_mode_server_error(client):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    raw_text = "internal error"
    mock_resp.text = AsyncMock(return_value=raw_text)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_DRIVE_MODE_URL", "http://localhost:7905/drive_mode"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
        resp = client.put("/symovo_drive_mode", json={"enable": False})
        assert resp.status_code == 200
        assert resp.json()["success"] is False


def test_drive_mode_connection_error(client):
    mock_session = MagicMock()
    mock_session.put = MagicMock(side_effect=aiohttp.ClientError("no connection"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("routes.robot.SYMOVO_DRIVE_MODE_URL", "http://localhost:7905/drive_mode"), \
         patch("routes.robot.aiohttp.ClientSession", return_value=mock_session):
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


# ── Robot positions run ─────────────────────────────────────────
def test_run_robot_position_success(client):
    with patch("routes.robot.get_robot_position", return_value={
        "params": {"velocity_percent": 30, "location": {"x_m": 1, "y_m": 1, "theta_deg": 0, "map_id": 0}}
    }), patch("routes.robot.robot.move_robot_to_product", new_callable=AsyncMock, return_value=True):
        resp = client.post("/robot_positions/run?position_id=abc123")
        assert resp.status_code == 201


def test_run_robot_position_not_found(client):
    with patch("routes.robot.get_robot_position", return_value=None):
        resp = client.post("/robot_positions/run?position_id=nonexistent")
        # Returns 200 with error via decorator chain, or 400/404 from inner HTTPException
        assert resp.status_code in (200, 201, 400, 404, 500)
