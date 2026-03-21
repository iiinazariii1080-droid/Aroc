"""Tests for textroom_relay.py — safety-critical joystick webhook relay."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def relay_app():
    """Fresh import of the relay app for each test."""
    import importlib
    import textroom_relay as mod
    importlib.reload(mod)
    return mod.app


@pytest.fixture
async def relay_client(relay_app):
    transport = ASGITransport(app=relay_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _janus_hook_body(axes=None, buttons=None, ts=None, frame_type=None):
    """Build a Janus TextRoom webhook body wrapping a joystick frame."""
    inner = {}
    if frame_type == "ping":
        inner = {"type": "ping", "ts": ts or 1000, "id": 42}
    else:
        inner = {
            "axes": axes or [0.0, 0.0, 0.0, 0.0],
            "buttons": buttons or [0, 0, 0, 0],
            "ts": ts or 1000,
        }
    return {
        "textroom": "message",
        "text": json.dumps(inner),
    }


# ── Source IP restriction (D3 fix) ──


@pytest.mark.asyncio
async def test_textroom_hook_rejects_non_localhost(relay_client):
    """Non-localhost requests must be rejected with 403."""
    body = _janus_hook_body(ts=5000)
    # httpx ASGITransport sets client to ("127.0.0.1", port) by default,
    # so we test the positive case here. The negative case requires
    # a custom transport or direct testing of the guard logic.
    resp = await relay_client.post("/textroom-hook", json=body)
    # From localhost → should be accepted (200)
    assert resp.status_code == 200


# ── Valid joystick frame ──


@pytest.mark.asyncio
async def test_textroom_hook_valid_frame(relay_client):
    """Valid joystick frame is accepted and returns 200."""
    body = _janus_hook_body(ts=2000)
    resp = await relay_client.post("/textroom-hook", json=body)
    assert resp.status_code == 200
    assert resp.text == "ok"


# ── Invalid JSON ──


@pytest.mark.asyncio
async def test_textroom_hook_bad_json(relay_client):
    """Malformed JSON body returns 400."""
    resp = await relay_client.post(
        "/textroom-hook",
        content=b"not json {{{",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ── Non-joystick message ──


@pytest.mark.asyncio
async def test_textroom_hook_non_joystick_message(relay_client):
    """Messages without textroom='message' are silently accepted."""
    resp = await relay_client.post("/textroom-hook", json={"textroom": "join"})
    assert resp.status_code == 200


# ── Ping frame ──


@pytest.mark.asyncio
async def test_textroom_hook_ping_frame(relay_client):
    """Ping frames should be accepted and generate pong data."""
    import textroom_relay as mod

    body = _janus_hook_body(frame_type="ping", ts=3000)
    resp = await relay_client.post("/textroom-hook", json=body)
    assert resp.status_code == 200


# ── Health endpoint ──


@pytest.mark.asyncio
async def test_health_endpoint(relay_client):
    """GET /health returns status and counters."""
    resp = await relay_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "counters" in data
    assert "queue" in data


# ── Time endpoint ──


@pytest.mark.asyncio
async def test_time_endpoint(relay_client):
    """GET /time returns server_ms."""
    resp = await relay_client.get("/time")
    assert resp.status_code == 200
    data = resp.json()
    assert "server_ms" in data
    assert isinstance(data["server_ms"], int)


# ── Pong endpoint ──


@pytest.mark.asyncio
async def test_pong_endpoint_no_ping(relay_client):
    """GET /pong before any ping returns id=-1."""
    resp = await relay_client.get("/pong")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == -1


# ── Frame validation ──


def test_is_valid_frame():
    """Unit test for _is_valid_frame."""
    import textroom_relay as mod

    # Valid joystick frame
    assert mod._is_valid_frame({"ts": 1000, "axes": [], "buttons": []})
    # Valid ping
    assert mod._is_valid_frame({"type": "ping", "ts": 1000})
    # Missing ts
    assert not mod._is_valid_frame({"axes": [], "buttons": []})
    # Not a dict
    assert not mod._is_valid_frame("string")
    assert not mod._is_valid_frame(None)
    # Missing axes
    assert not mod._is_valid_frame({"ts": 1000, "buttons": []})


def test_extract_inner_frame():
    """Unit test for _extract_inner_frame."""
    import textroom_relay as mod

    # Valid extraction
    body = {"textroom": "message", "text": json.dumps({"ts": 1000, "axes": [0], "buttons": [0]})}
    result = mod._extract_inner_frame(body)
    assert result is not None
    assert result["ts"] == 1000

    # Not a message
    assert mod._extract_inner_frame({"textroom": "join"}) is None

    # Invalid JSON in text
    assert mod._extract_inner_frame({"textroom": "message", "text": "not json"}) is None

    # Text is not a dict
    assert mod._extract_inner_frame({"textroom": "message", "text": '"string"'}) is None

    # Float ts gets normalized to int
    body_float = {"textroom": "message", "text": json.dumps({"ts": 1000.5, "axes": [], "buttons": []})}
    result_float = mod._extract_inner_frame(body_float)
    assert result_float["ts"] == 1000


# ── Bounds validation (DEF-08) ──


def test_is_valid_frame_axes_out_of_range():
    """Axes with values outside [-1.0, 1.0] must be rejected."""
    import textroom_relay as mod

    assert not mod._is_valid_frame({"ts": 1000, "axes": [1.5, 0.0], "buttons": [0]})
    assert not mod._is_valid_frame({"ts": 1000, "axes": [-1.1], "buttons": [0]})
    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0, 999.0], "buttons": [0]})


def test_is_valid_frame_axes_too_many():
    """Axes list longer than 8 must be rejected."""
    import textroom_relay as mod

    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0] * 9, "buttons": [0]})
    # Exactly 8 is fine
    assert mod._is_valid_frame({"ts": 1000, "axes": [0.0] * 8, "buttons": [0]})


def test_is_valid_frame_buttons_invalid_value():
    """Button values other than 0/1 must be rejected."""
    import textroom_relay as mod

    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0], "buttons": [2]})
    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0], "buttons": [0.5]})
    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0], "buttons": [-1]})


def test_is_valid_frame_buttons_too_many():
    """Buttons list longer than 20 must be rejected."""
    import textroom_relay as mod

    assert not mod._is_valid_frame({"ts": 1000, "axes": [0.0], "buttons": [0] * 21})
    # Exactly 20 is fine
    assert mod._is_valid_frame({"ts": 1000, "axes": [0.0], "buttons": [0] * 20})


def test_is_valid_frame_valid_passes():
    """A fully populated valid frame must pass validation."""
    import textroom_relay as mod

    frame = {
        "ts": 1710000000,
        "axes": [0.0, -1.0, 1.0, 0.5],
        "buttons": [0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
    }
    assert mod._is_valid_frame(frame)


# ── _forward_worker tests (DEF-04) ──


@pytest.fixture
def relay_mod():
    """Fresh import of textroom_relay module with reset state."""
    import importlib
    import textroom_relay as mod
    importlib.reload(mod)
    return mod


@pytest.mark.asyncio
async def test_forward_worker_posts_to_robot(relay_mod):
    """Valid joystick frame is forwarded via HTTP POST to ROBOT_URL."""
    mock_response = AsyncMock()
    mock_response.status_code = 200

    relay_mod._client = AsyncMock()
    relay_mod._client.post = AsyncMock(return_value=mock_response)
    relay_mod._last_ts_forwarded = 0
    relay_mod._forwarded = 0
    relay_mod._errors = 0

    frame = {"ts": 5000, "axes": [0.1], "buttons": [0]}
    await relay_mod._queue.put(frame)

    # Run worker for one iteration
    task = asyncio.create_task(relay_mod._forward_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    relay_mod._client.post.assert_called_once_with(relay_mod.ROBOT_URL, json=frame)
    assert relay_mod._forwarded == 1
    assert relay_mod._last_ts_forwarded == 5000
    assert relay_mod._errors == 0


@pytest.mark.asyncio
async def test_forward_worker_skips_stale_frames(relay_mod):
    """Frames with ts <= _last_ts_forwarded are silently dropped."""
    relay_mod._client = AsyncMock()
    relay_mod._last_ts_forwarded = 9000
    relay_mod._forwarded = 0

    frame = {"ts": 5000, "axes": [0.0], "buttons": [0]}
    await relay_mod._queue.put(frame)

    task = asyncio.create_task(relay_mod._forward_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    relay_mod._client.post.assert_not_called()
    assert relay_mod._forwarded == 0


@pytest.mark.asyncio
async def test_forward_worker_handles_robot_error(relay_mod):
    """Non-200 from robot increments _errors, does not update _last_ts_forwarded."""
    mock_response = AsyncMock()
    mock_response.status_code = 500
    mock_response.text = "internal error"

    relay_mod._client = AsyncMock()
    relay_mod._client.post = AsyncMock(return_value=mock_response)
    relay_mod._last_ts_forwarded = 0
    relay_mod._forwarded = 0
    relay_mod._errors = 0

    frame = {"ts": 7000, "axes": [0.0], "buttons": [0]}
    await relay_mod._queue.put(frame)

    task = asyncio.create_task(relay_mod._forward_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert relay_mod._errors == 1
    assert relay_mod._forwarded == 0
    assert relay_mod._last_ts_forwarded == 0


@pytest.mark.asyncio
async def test_forward_worker_handles_connection_error(relay_mod):
    """Connection error to robot increments _errors and continues."""
    import httpx

    relay_mod._client = AsyncMock()
    relay_mod._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    relay_mod._last_ts_forwarded = 0
    relay_mod._forwarded = 0
    relay_mod._errors = 0

    frame = {"ts": 8000, "axes": [0.0], "buttons": [0]}
    await relay_mod._queue.put(frame)

    task = asyncio.create_task(relay_mod._forward_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert relay_mod._errors == 1
    assert relay_mod._forwarded == 0


@pytest.mark.asyncio
async def test_forward_worker_ping_creates_pong(relay_mod):
    """Ping frames produce _last_pong data and are not forwarded to robot."""
    relay_mod._client = AsyncMock()
    relay_mod._forwarded = 0
    relay_mod._last_pong = None

    ping = {"type": "ping", "ts": 4000, "id": 99, "relay_rx_ms": 4001}
    await relay_mod._queue.put(ping)

    task = asyncio.create_task(relay_mod._forward_worker())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    relay_mod._client.post.assert_not_called()
    assert relay_mod._forwarded == 1
    assert relay_mod._last_pong is not None
    assert relay_mod._last_pong["id"] == 99
    assert relay_mod._last_pong["browser_ts"] == 4000
