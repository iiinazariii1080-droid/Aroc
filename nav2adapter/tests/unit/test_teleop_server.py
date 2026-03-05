"""Tests for app/teleop_server.py — teleop proxy endpoint."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import aiohttp

from app.teleop_server import teleop_app, _robot_url, _ssl_context


@pytest.fixture
def client():
    # Pre-set a mock session on app.state so tests don't need the lifespan
    teleop_app.state.session = MagicMock()
    return TestClient(teleop_app, raise_server_exceptions=False)


# ── Utility functions ────────────────────────────────────────────
def test_robot_url():
    url = _robot_url()
    assert "/v0/agv/" in url
    assert url.endswith("/move/speed")


def test_ssl_context_insecure():
    ctx = _ssl_context()
    assert ctx is not None
    assert ctx.check_hostname is False


def test_ssl_context_secure():
    with patch("app.teleop_server.settings") as mock_settings:
        mock_settings.symovo_allow_invalid_certs = False
        result = _ssl_context()
        assert result is None


# ── move_speed endpoint ──────────────────────────────────────────
def test_move_speed_success(client):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "ok"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={
        "speed": 0.5, "angular_speed": 0.0, "duration": 0.25
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_move_speed_202(client):
    mock_resp = AsyncMock()
    mock_resp.status = 202
    mock_resp.json = AsyncMock(return_value={"status": "accepted"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={"speed": 0.1})
    assert resp.status_code == 200


def test_move_speed_backend_error(client):
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.text = AsyncMock(return_value="bad request")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={"speed": 0.1})
    assert resp.status_code == 502


def test_move_speed_connection_error(client):
    mock_session = MagicMock()
    mock_session.put = MagicMock(side_effect=aiohttp.ClientError("timeout"))
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={"speed": 0.1})
    assert resp.status_code == 503


def test_move_speed_defaults(client):
    """When fields are None, defaults from settings are used."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "ok"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={})
    assert resp.status_code == 200


def test_move_speed_post(client):
    """POST /move/speed should also work."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "ok"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.post("/move/speed", json={"linear_dir": 1, "angular_dir": -1})
    assert resp.status_code == 200


def test_move_speed_non_dict_response(client):
    """When JSON response is not a dict, return default."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value="not a dict")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={"speed": 0.1})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_move_speed_json_parse_error(client):
    """When response JSON parsing fails, return default ok."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(side_effect=ValueError("bad json"))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.put = MagicMock(return_value=mock_resp)
    mock_session.closed = False

    teleop_app.state.session = mock_session
    resp = client.put("/move/speed", json={"speed": 0.1})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
