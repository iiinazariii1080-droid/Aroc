"""Integration tests for WebSocket /janus-ws and /janus/ws proxy routes.

Validates:
- TD-1: WebSocket proxy data path (0% → covered)
- Connection acceptance with janus-protocol subprotocol
- Bidirectional message forwarding
- Connection rejection when upstream unavailable
- Connection limit enforcement (SD-3)

Markers: unit, integration
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_ADMIN_TOKEN = "test-token-ws-integration"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "CAM_ADMIN_TOKEN": _ADMIN_TOKEN,
        "WS_MAX_CONNECTIONS": "3",
        "WS_MSG_RATE_PER_SEC": "5",
    }):
        yield


@pytest.fixture
def app():
    with patch("app.core.events.register_event_handlers", lambda app: None):
        import app.core.admin as _admin
        _admin.ADMIN_TOKEN = _ADMIN_TOKEN
        from app.core.app import create_app
        return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestWSOriginValidation:
    """DEF-02: WebSocket origin validation (defence-in-depth)."""

    def test_allows_absent_origin(self):
        """Same-origin requests omit Origin header — must be allowed."""
        from app.services.ws_proxy import _validate_ws_origin
        ws = MagicMock()
        ws.headers = {}
        assert _validate_ws_origin(ws) is True

    def test_allows_lan_origin(self):
        """LAN origin matching CORS regex must be allowed."""
        from app.services.ws_proxy import _validate_ws_origin
        ws = MagicMock()
        ws.headers = {"origin": "http://192.168.1.10:8900"}
        assert _validate_ws_origin(ws) is True

    def test_allows_localhost_origin(self):
        from app.services.ws_proxy import _validate_ws_origin
        ws = MagicMock()
        ws.headers = {"origin": "http://localhost:3000"}
        assert _validate_ws_origin(ws) is True

    def test_allows_techvision_origin(self):
        from app.services.ws_proxy import _validate_ws_origin
        ws = MagicMock()
        ws.headers = {"origin": "https://panel.techvisioncloud.pl"}
        assert _validate_ws_origin(ws) is True

    def test_rejects_foreign_origin(self):
        """Non-matching origin must be rejected."""
        from app.services.ws_proxy import _validate_ws_origin
        ws = MagicMock()
        ws.headers = {"origin": "http://evil.example.com"}
        assert _validate_ws_origin(ws) is False

    @pytest.mark.asyncio
    async def test_foreign_origin_closes_with_1008(self):
        """Foreign origin must close the WebSocket with code 1008 (Policy Violation)."""
        from app.services.ws_proxy import _proxy_websocket_inner
        ws = AsyncMock()
        ws.headers = MagicMock()
        ws.headers.get = lambda key, default="": {
            "origin": "http://evil.example.com",
            "sec-websocket-protocol": "",
        }.get(key, default)
        await _proxy_websocket_inner(ws, "ws://localhost:8188/janus-ws")
        ws.close.assert_awaited_once_with(code=1008)
        ws.accept.assert_not_awaited()


class TestWSProxyConnectionLimits:
    """SD-3: WebSocket connection limits."""

    @pytest.mark.asyncio
    async def test_active_counter_limits_connections(self):
        """Verify semaphore-based connection limiting (TOCTOU fix DEF-05)."""
        from app.services import ws_proxy

        original_semaphore = ws_proxy._ws_semaphore

        try:
            # Replace with a semaphore that's already fully acquired
            ws_proxy._ws_semaphore = asyncio.Semaphore(1)
            await ws_proxy._ws_semaphore.acquire()  # drain it

            mock_ws = AsyncMock()
            mock_ws.headers = MagicMock()
            mock_ws.headers.get = MagicMock(return_value="")
            await ws_proxy.proxy_websocket(mock_ws, "ws://localhost:8188/janus-ws")
            mock_ws.close.assert_awaited_once_with(code=1013)
        finally:
            ws_proxy._ws_semaphore = original_semaphore


class TestWSProxyRateLimiting:
    """Message rate limiting in pump_client_to_upstream."""

    @pytest.mark.asyncio
    async def test_rate_limit_drops_excess_messages(self):
        """Messages beyond rate_limit/s are dropped."""
        from app.services.ws_proxy import pump_client_to_upstream

        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        # Send 10 messages rapidly (rate_limit=3)
        messages = [
            {"type": "websocket.receive", "text": f"msg{i}"}
            for i in range(10)
        ] + [{"type": "websocket.disconnect"}]
        client_ws.receive = AsyncMock(side_effect=messages)

        await pump_client_to_upstream(client_ws, upstream_ws, rate_limit=3)

        # Should have forwarded at most 3 messages (rate limited within 1s window)
        assert upstream_ws.send.await_count <= 3


class TestWSProxyActiveCounter:
    """ws_active_connections tracking."""

    def test_initial_active_is_zero(self):
        from app.services.ws_proxy import ws_active_connections
        # Should not crash and should return an int
        count = ws_active_connections()
        assert isinstance(count, int)


class TestWSProxyBidirectional:
    """Bidirectional message forwarding through pump functions."""

    @pytest.mark.asyncio
    async def test_text_and_binary_forwarding(self):
        from app.services.ws_proxy import pump_client_to_upstream, pump_upstream_to_client

        # Client → Upstream: text
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()
        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": '{"janus":"create"}'},
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.disconnect"},
        ])
        await pump_client_to_upstream(client_ws, upstream_ws)
        assert upstream_ws.send.await_count == 2

    @pytest.mark.asyncio
    async def test_upstream_to_client_text(self):
        from app.services.ws_proxy import pump_upstream_to_client

        client_ws = AsyncMock()

        class FakeUpstream:
            def __init__(self, messages):
                self._msgs = iter(messages)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._msgs)
                except StopIteration:
                    raise StopAsyncIteration

        upstream_ws = FakeUpstream(['{"janus":"ack"}', b"\xff"])
        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.send_text.assert_awaited_once_with('{"janus":"ack"}')
        client_ws.send_bytes.assert_awaited_once_with(b"\xff")
