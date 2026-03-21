"""Tests for the WebSocket proxy module."""
from __future__ import annotations

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ws_proxy import (
    ssl_ctx_for,
    pump_client_to_upstream,
    pump_upstream_to_client,
)


class TestSslCtxFor:
    """Tests for ssl_ctx_for()."""

    def test_returns_none_for_ws(self):
        assert ssl_ctx_for("ws://localhost:8188/janus-ws") is None

    def test_returns_context_for_wss(self):
        ctx = ssl_ctx_for("wss://example.com/ws")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED


class TestPumpClientToUpstream:
    """Tests for pump_client_to_upstream()."""

    @pytest.mark.asyncio
    async def test_forwards_text_message(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        # Simulate: one text message, then disconnect
        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": '{"janus":"keepalive"}'},
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.send.assert_awaited_once_with('{"janus":"keepalive"}')
        upstream_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_binary_message(self):
        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "bytes": b"\x00\x01\x02"},
            {"type": "websocket.disconnect"},
        ])

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.send.assert_awaited_once_with(b"\x00\x01\x02")

    @pytest.mark.asyncio
    async def test_handles_client_disconnect_exception(self):
        from starlette.websockets import WebSocketDisconnect

        client_ws = AsyncMock()
        upstream_ws = AsyncMock()

        client_ws.receive = AsyncMock(side_effect=WebSocketDisconnect())

        await pump_client_to_upstream(client_ws, upstream_ws)
        upstream_ws.close.assert_awaited_once()


class _AsyncIter:
    """Helper: turn a list into an async iterator for ``async for``."""

    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class TestPumpUpstreamToClient:
    """Tests for pump_upstream_to_client()."""

    @pytest.mark.asyncio
    async def test_forwards_text_to_client(self):
        client_ws = AsyncMock()
        upstream_ws = _AsyncIter(['{"janus":"ack"}'])
        # Add close method for cleanup
        upstream_ws.close = AsyncMock()

        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.send_text.assert_awaited_once_with('{"janus":"ack"}')

    @pytest.mark.asyncio
    async def test_forwards_bytes_to_client(self):
        client_ws = AsyncMock()
        upstream_ws = _AsyncIter([b"\xff\xfe"])
        upstream_ws.close = AsyncMock()

        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.send_bytes.assert_awaited_once_with(b"\xff\xfe")

    @pytest.mark.asyncio
    async def test_handles_connection_closed(self):
        from websockets.exceptions import ConnectionClosedOK

        client_ws = AsyncMock()

        class _Raise:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise ConnectionClosedOK(None, None)

        upstream_ws = _Raise()

        await pump_upstream_to_client(client_ws, upstream_ws)
        client_ws.close.assert_awaited_once()
