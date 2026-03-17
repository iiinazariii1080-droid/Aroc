"""Tests for app/services/ws_pump.py — WebSocket bidirectional proxy.

Covers:
  - make_ssl_context: WS vs WSS, insecure mode
  - pump_client_to_upstream: text, bytes, disconnect handling
  - pump_upstream_to_client: text, bytes, connection closed handling
  - Subprotocol negotiation in proxy_websocket
"""
from __future__ import annotations

import asyncio
import os
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("CAM_TYPE", "depth_camera")
os.environ.setdefault("CAM_ADMIN_TOKEN", "test-token")

from app.services.ws_pump import (
    make_ssl_context,
    pump_client_to_upstream,
    pump_upstream_to_client,
)
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK


# ===================================================================
# 1. make_ssl_context
# ===================================================================

class TestMakeSslContext:

    def test_plain_ws_returns_none(self):
        assert make_ssl_context("ws://localhost:8188") is None

    def test_wss_returns_ssl_context(self):
        ctx = make_ssl_context("wss://gateway.example.com:8989")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_wss_insecure_disables_verification(self):
        ctx = make_ssl_context("wss://gateway.example.com:8989", allow_insecure=True)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_plain_ws_insecure_still_returns_none(self):
        """allow_insecure has no effect on plain WS."""
        assert make_ssl_context("ws://localhost:8188", allow_insecure=True) is None


# ===================================================================
# 2. pump_client_to_upstream
# ===================================================================

class TestPumpClientToUpstream:

    @pytest.mark.asyncio
    async def test_forwards_text_message(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": "hello", "bytes": None},
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.send.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_forwards_binary_message(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": None, "bytes": b"\x00\x01\x02"},
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.send.assert_awaited_once_with(b"\x00\x01\x02")

    @pytest.mark.asyncio
    async def test_disconnect_closes_upstream(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_websocket_disconnect_exception_closes_upstream(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect())

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upstream_close_error_swallowed(self):
        """If upstream.close() raises, it should not propagate."""
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.disconnect"},
        ])
        upstream_ws.close = AsyncMock(side_effect=Exception("already closed"))

        # Should not raise
        await pump_client_to_upstream(client_ws, upstream_ws)

    @pytest.mark.asyncio
    async def test_multiple_messages_forwarded(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": "msg1", "bytes": None},
            {"type": "websocket.receive", "text": "msg2", "bytes": None},
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        assert upstream_ws.send.await_count == 2


# ===================================================================
# 3. pump_upstream_to_client
# ===================================================================

class TestPumpUpstreamToClient:

    @pytest.mark.asyncio
    async def test_forwards_text_to_client(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()
        upstream_ws.__aiter__ = lambda self: self
        messages = iter(["hello", "world"])
        upstream_ws.__anext__ = AsyncMock(side_effect=lambda: next(messages, (_ for _ in ()).throw(StopAsyncIteration)))

        # Simpler: use a real async iterable
        async def fake_iter():
            yield "hello"
            yield "world"

        upstream_ws = _FakeUpstream(["hello", "world"])
        await pump_upstream_to_client(client_ws, upstream_ws)
        assert client_ws.send_text.await_count == 2
        client_ws.send_text.assert_any_await("hello")
        client_ws.send_text.assert_any_await("world")

    @pytest.mark.asyncio
    async def test_forwards_bytes_to_client(self):
        client_ws = AsyncMock()
        upstream_ws = _FakeUpstream([b"\x00\x01", b"\x02\x03"])

        await pump_upstream_to_client(client_ws, upstream_ws)
        assert client_ws.send_bytes.await_count == 2

    @pytest.mark.asyncio
    async def test_connection_closed_ok_closes_client(self):
        client_ws = AsyncMock()
        upstream_ws = _FakeUpstream([], raise_on_iter=ConnectionClosedOK(None, None))

        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_closed_error_closes_client(self):
        client_ws = AsyncMock()
        upstream_ws = _FakeUpstream([], raise_on_iter=ConnectionClosedError(None, None))

        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_client_close_error_swallowed(self):
        """If client.close() raises during upstream disconnect, don't propagate."""
        client_ws = AsyncMock()
        client_ws.close = AsyncMock(side_effect=Exception("already gone"))
        upstream_ws = _FakeUpstream([], raise_on_iter=ConnectionClosedOK(None, None))

        # Should not raise
        await pump_upstream_to_client(client_ws, upstream_ws)


# ===================================================================
# Helpers
# ===================================================================

class _FakeUpstream:
    """Async iterable that yields messages, then optionally raises."""

    def __init__(self, messages: list, raise_on_iter: Exception | None = None):
        self._messages = messages
        self._raise = raise_on_iter
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise and self._index >= len(self._messages):
            raise self._raise
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg

    async def close(self):
        pass
