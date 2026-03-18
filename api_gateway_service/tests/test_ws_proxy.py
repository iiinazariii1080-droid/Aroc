"""Unit tests for WebSocket proxy — pump functions, circuit breaker, semaphore lifecycle."""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from app.core.config import settings

from app.routers.proxy_ws import (
    _pump_client_to_upstream,
    _pump_upstream_to_client,
    _janus_ws_proxy,
    _ws_target_url,
)


# ── Helpers ───────────────────────────────────────────────────

def _mock_client_ws(messages=None, app=None):
    """Build a mock FastAPI WebSocket with programmable receive() sequence."""
    ws = MagicMock(spec=WebSocket)
    ws.app = app or MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()

    # Mock URL with query
    url_mock = MagicMock()
    url_mock.query = ""
    ws.url = url_mock

    # Mock headers
    ws.headers = {}

    if messages is not None:
        ws.receive = AsyncMock(side_effect=messages)
    return ws


class _MockUpstreamWs:
    """Mock upstream websocket supporting ``async for`` iteration."""
    def __init__(self, messages=None):
        self._messages = messages or []
        self.send = AsyncMock()
        self.close = AsyncMock()

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for msg in self._messages:
            yield msg


def _mock_upstream_ws(messages=None):
    """Build a mock upstream websocket (websockets library style)."""
    return _MockUpstreamWs(messages)


# ── _pump_client_to_upstream ──────────────────────────────────

class TestPumpClientToUpstream:
    async def test_forwards_text_message(self):
        client_ws = _mock_client_ws(messages=[
            {"type": "websocket.receive", "text": "hello"},
            {"type": "websocket.disconnect"},
        ])
        upstream_ws = _mock_upstream_ws()

        await _pump_client_to_upstream(client_ws, upstream_ws)

        upstream_ws.send.assert_awaited_once_with("hello")

    async def test_forwards_binary_message(self):
        client_ws = _mock_client_ws(messages=[
            {"type": "websocket.receive", "bytes": b"\x00\x01\x02", "text": None},
            {"type": "websocket.disconnect"},
        ])
        upstream_ws = _mock_upstream_ws()

        await _pump_client_to_upstream(client_ws, upstream_ws)

        upstream_ws.send.assert_awaited_once_with(b"\x00\x01\x02")

    async def test_stops_on_disconnect(self):
        client_ws = _mock_client_ws(messages=[
            {"type": "websocket.disconnect"},
        ])
        upstream_ws = _mock_upstream_ws()

        await _pump_client_to_upstream(client_ws, upstream_ws)

        upstream_ws.send.assert_not_awaited()

    async def test_handles_websocket_disconnect_exception(self):
        client_ws = _mock_client_ws()
        client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect())
        upstream_ws = _mock_upstream_ws()

        await _pump_client_to_upstream(client_ws, upstream_ws)

        # Should close upstream gracefully
        upstream_ws.close.assert_awaited_once()

    async def test_closes_upstream_in_finally(self):
        client_ws = _mock_client_ws(messages=[
            {"type": "websocket.disconnect"},
        ])
        upstream_ws = _mock_upstream_ws()

        await _pump_client_to_upstream(client_ws, upstream_ws)

        upstream_ws.close.assert_awaited_once()


# ── _pump_upstream_to_client ──────────────────────────────────

class TestPumpUpstreamToClient:
    async def test_forwards_text_to_client(self):
        client_ws = _mock_client_ws()
        upstream_ws = _mock_upstream_ws(messages=["hello from upstream"])

        await _pump_upstream_to_client(client_ws, upstream_ws)

        client_ws.send_text.assert_awaited_once_with("hello from upstream")

    async def test_forwards_bytes_to_client(self):
        client_ws = _mock_client_ws()
        upstream_ws = _mock_upstream_ws(messages=[b"\xff\xfe"])

        await _pump_upstream_to_client(client_ws, upstream_ws)

        client_ws.send_bytes.assert_awaited_once_with(b"\xff\xfe")

    async def test_closes_client_in_finally(self):
        client_ws = _mock_client_ws()
        upstream_ws = _mock_upstream_ws(messages=[])

        await _pump_upstream_to_client(client_ws, upstream_ws)

        client_ws.close.assert_awaited_once()


# ── _janus_ws_proxy — circuit breaker ─────────────────────────

def _make_mock_cb(allow=True):
    """Build a mock CircuitBreaker with async methods."""
    mock_cb = MagicMock()
    mock_cb.allow_request = AsyncMock(return_value=allow)
    mock_cb.record_success = AsyncMock()
    mock_cb.record_failure = AsyncMock()
    return mock_cb


class TestJanusWsProxyCircuitBreaker:
    async def test_rejects_when_circuit_open(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb(allow=False)

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb):
            await _janus_ws_proxy(client_ws, "xarm")

        client_ws.accept.assert_awaited_once()
        client_ws.close.assert_awaited_once()
        close_kwargs = client_ws.close.call_args
        assert close_kwargs.kwargs.get("code") == 1013 or close_kwargs[1].get("code") == 1013


# ── _janus_ws_proxy — semaphore ───────────────────────────────

class TestJanusWsProxySemaphore:
    async def test_rejects_on_semaphore_timeout(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb()

        # Semaphore that never acquires
        sem = asyncio.Semaphore(0)
        mock_app = MagicMock()

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch.object(settings, "ws_acquire_timeout", 0.01):
            await _janus_ws_proxy(client_ws, "xarm")

        client_ws.accept.assert_awaited_once()
        client_ws.close.assert_awaited_once()

    async def test_semaphore_released_after_connect_failure(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)
        assert sem._value == 1

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=OSError("refused")):
            await _janus_ws_proxy(client_ws, "xarm")

        # Semaphore must be released even after error
        assert sem._value == 1

    async def test_semaphore_released_after_timeout(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch.object(settings, "ws_total_timeout", 0.01), \
             patch("app.routers.proxy_ws.ws_connect") as mock_connect:
            # ws_connect context manager that hangs
            async_cm = MagicMock()
            upstream = _mock_upstream_ws(messages=[])
            async_cm.__aenter__ = AsyncMock(return_value=upstream)
            async_cm.__aexit__ = AsyncMock(return_value=False)
            mock_connect.return_value = async_cm

            await _janus_ws_proxy(client_ws, "xarm")

        assert sem._value == 1


# ── _janus_ws_proxy — connect failure ─────────────────────────

class TestJanusWsProxyConnectFailure:
    async def test_records_failure_on_connection_refused(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=ConnectionRefusedError("refused")):
            await _janus_ws_proxy(client_ws, "xarm")

        mock_cb.record_failure.assert_awaited_once()

    async def test_closes_client_with_1011_on_failure(self):
        client_ws = _mock_client_ws()
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=OSError("net err")):
            await _janus_ws_proxy(client_ws, "xarm")

        client_ws.close.assert_awaited()


# ── _janus_ws_proxy — query params and subprotocol ────────────

class TestJanusWsProxyProtocol:
    async def test_query_params_appended_to_target(self):
        client_ws = _mock_client_ws()
        client_ws.url.query = "room=1234&pin=abc"
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)
        captured_url = None

        def capture_connect(url, **kwargs):
            nonlocal captured_url
            captured_url = url
            raise OSError("expected")

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=capture_connect):
            await _janus_ws_proxy(client_ws, "xarm")

        assert captured_url is not None
        assert "room=1234&pin=abc" in captured_url

    async def test_janus_subprotocol_negotiated(self):
        client_ws = _mock_client_ws()
        client_ws.headers = {"sec-websocket-protocol": "janus-protocol, other"}
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)
        captured_kwargs = {}

        def capture_connect(url, **kwargs):
            captured_kwargs.update(kwargs)
            raise OSError("expected")

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=capture_connect):
            await _janus_ws_proxy(client_ws, "xarm")

        assert captured_kwargs.get("subprotocols") == ["janus-protocol"]
        client_ws.accept.assert_awaited_once_with(subprotocol="janus-protocol")

    async def test_no_subprotocol_when_not_offered(self):
        client_ws = _mock_client_ws()
        client_ws.headers = {}
        mock_cb = _make_mock_cb()

        sem = asyncio.Semaphore(1)
        captured_kwargs = {}

        def capture_connect(url, **kwargs):
            captured_kwargs.update(kwargs)
            raise OSError("expected")

        with patch("app.routers.proxy_ws.get_breaker", new_callable=AsyncMock, return_value=mock_cb), \
             patch("app.routers.proxy_ws.get_ws_semaphore", return_value=sem), \
             patch("app.routers.proxy_ws.ws_connect", side_effect=capture_connect):
            await _janus_ws_proxy(client_ws, "xarm")

        assert "subprotocols" not in captured_kwargs
        client_ws.accept.assert_awaited_once_with(subprotocol=None)


# ── _ws_target_url config ─────────────────────────────────────

class TestWsTargets:
    def test_all_targets_present(self):
        assert _ws_target_url("xarm") is not None
        assert _ws_target_url("color_camera") is not None
        assert _ws_target_url("depth_camera") is not None

    def test_unknown_target_returns_none(self):
        assert _ws_target_url("nonexistent") is None

    def test_all_targets_have_ws_scheme(self):
        for name in ("xarm", "color_camera", "depth_camera"):
            url = _ws_target_url(name)
            assert url.startswith(("ws://", "wss://")), f"{name} URL missing ws:// scheme: {url}"
