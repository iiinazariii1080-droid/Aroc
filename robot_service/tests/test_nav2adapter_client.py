"""Tests for Nav2AdapterClient — thin AGV proxy through nav2adapter."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.nav2adapter_client import Nav2AdapterClient
from exceptions import DeviceConnectionError, DeviceError


BASE = "http://localhost:7905"


def _make_client() -> Nav2AdapterClient:
    return Nav2AdapterClient(BASE)


def _mock_response(status: int = 200, json_data: dict | None = None):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(resp):
    session = AsyncMock()
    session.get = MagicMock(return_value=resp)
    session.post = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ── GET endpoints ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pose_ok():
    data = {"pose": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0}}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().pose()

    assert result == data
    session.get.assert_called_once()
    call_args = session.get.call_args
    assert "/pose" in str(call_args)


@pytest.mark.asyncio
async def test_status_ok():
    data = {"online": True, "enabled": True, "state_flags": {}}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().status()

    assert result["online"] is True


@pytest.mark.asyncio
async def test_safety_state_ok():
    data = {"safety_lockout": False, "reason": None}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().safety_state()

    assert result["safety_lockout"] is False


# ── POST endpoints ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fault_reset_ok():
    data = {"status": "ok", "deleted": 0}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().fault_reset()

    assert result["status"] == "ok"
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_go_to_pose_ok():
    data = {"transport_id": 42}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().go_to_pose(
            x_m=1.0, y_m=2.0, theta_deg=90, map_id=1, wait=False,
        )

    assert result["transport_id"] == 42
    session.post.assert_called_once()
    call_args = session.post.call_args
    assert "/go_to_pose" in str(call_args)
    body = call_args.kwargs.get("json") or call_args[1].get("json", {})
    assert body["x_m"] == 1.0
    assert body["y_m"] == 2.0


@pytest.mark.asyncio
async def test_go_to_charging_station_ok():
    data = {"station_id": 5, "activated": True}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().go_to_charging_station(5)

    assert result["activated"] is True
    call_args = session.post.call_args
    assert "/go_to_charging_station/5" in str(call_args)


@pytest.mark.asyncio
async def test_disable_all_charging_stations_ok():
    data = {"deactivated": 3, "all_inactive": True}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().disable_all_charging_stations()

    assert result["all_inactive"] is True


# ── Error handling ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_non_200_raises_device_error():
    resp = _mock_response(503, {"detail": {"error": "not ready"}})
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        with pytest.raises(DeviceError, match="nav2adapter /status HTTP 503"):
            await _make_client().status()


@pytest.mark.asyncio
async def test_post_non_200_raises_device_error():
    resp = _mock_response(409, {"detail": {"error": "busy"}})
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        with pytest.raises(DeviceError, match="nav2adapter /fault_reset HTTP 409"):
            await _make_client().fault_reset()


@pytest.mark.asyncio
async def test_network_error_raises_device_connection_error():
    from aiohttp import ClientConnectionError

    session = AsyncMock()
    session.get = MagicMock(side_effect=ClientConnectionError("unreachable"))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        with pytest.raises(DeviceConnectionError, match="nav2adapter unreachable"):
            await _make_client().pose()


@pytest.mark.asyncio
async def test_go_to_pose_optional_fields():
    """Verify optional fields (map_id, max_speed_m_s) are omitted when None."""
    data = {"transport_id": 1}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        await _make_client().go_to_pose(x_m=0, y_m=0)

    body = session.post.call_args.kwargs.get("json") or session.post.call_args[1].get("json", {})
    assert "map_id" not in body
    assert "max_speed_m_s" not in body
    assert body["wait"] is False  # default


# ── PUT endpoints ────────────────────────────────────────────────────────


def _mock_put_session(resp):
    """Mock session with .put() support."""
    session = AsyncMock()
    session.put = MagicMock(return_value=resp)
    session.closed = False
    return session


@pytest.mark.asyncio
async def test_drive_mode_enable_ok():
    resp = _mock_response(200, {"enabled": True})
    session = _mock_put_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().drive_mode(enable=True)

    assert result["enabled"] is True
    call_args = session.put.call_args
    assert "/drive_mode" in str(call_args)
    params = call_args.kwargs.get("params") or call_args[1].get("params", {})
    assert params["enable"] == "true"


@pytest.mark.asyncio
async def test_drive_mode_disable_ok():
    resp = _mock_response(200, {"enabled": False})
    session = _mock_put_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().drive_mode(enable=False)

    call_args = session.put.call_args
    params = call_args.kwargs.get("params") or call_args[1].get("params", {})
    assert params["enable"] == "false"


@pytest.mark.asyncio
async def test_teleop_move_ok():
    resp = _mock_response(200, {"status": "ok"})
    session = _mock_put_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        client = _make_client()
        client._robot_id = "test-robot"  # skip _resolve_robot_id HTTP call
        result = await client.teleop_move(speed=0.1, angular_speed=0.0, duration=0.25)

    assert result["status"] == "ok"
    call_args = session.put.call_args
    assert "/move/speed" in str(call_args)
    assert "test-robot" in str(call_args)
    body = call_args.kwargs.get("json") or call_args[1].get("json", {})
    assert body["speed"] == 0.1


@pytest.mark.asyncio
async def test_teleop_config_get_ok():
    data = {"duration": 0.25, "linear_m_s": 0.1, "angular_rad_s": 0.5}
    resp = _mock_response(200, data)
    session = _mock_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().teleop_config_get()

    assert result["duration"] == 0.25
    call_args = session.get.call_args
    assert "/teleop/config" in str(call_args)


@pytest.mark.asyncio
async def test_teleop_config_put_ok():
    data = {"duration": 0.5, "linear_m_s": 0.2, "angular_rad_s": 0.5}
    resp = _mock_response(200, data)
    session = _mock_put_session(resp)

    with patch("services.nav2adapter_client.aiohttp.ClientSession", return_value=session):
        result = await _make_client().teleop_config_put(json={"duration": 0.5, "linear_m_s": 0.2})

    assert result["duration"] == 0.5
    call_args = session.put.call_args
    assert "/teleop/config" in str(call_args)
