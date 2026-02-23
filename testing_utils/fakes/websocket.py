"""Fake WebSocket helpers for testing WebSocket endpoints and clients.

Provides FakeWebSocket for testing FastAPI WebSocket endpoints without
a real HTTP connection, and FakeWSConnection for testing WebSocket client code.

Usage::

    from testing_utils.fakes.websocket import FakeWebSocket

    ws = FakeWebSocket()
    await endpoint(ws)
    assert ws.sent_messages[0] == {"type": "status", "ok": True}
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


class FakeWebSocket:
    """In-memory stub for fastapi.WebSocket.

    Simulates a WebSocket connection for testing route handlers
    that accept ``websocket: WebSocket`` parameters.
    """

    def __init__(self, *, incoming: list[str | bytes | dict] | None = None) -> None:
        self._incoming: list[str | bytes | dict] = list(incoming or [])
        self._incoming_idx: int = 0
        self.sent_messages: list[Any] = []
        self.sent_bytes: list[bytes] = []
        self.accepted: bool = False
        self.closed: bool = False
        self.close_code: int | None = None
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.path_params: dict[str, str] = {}
        self.client: Any = type("Client", (), {"host": "127.0.0.1", "port": 9999})()
        self.state: Any = type("State", (), {})()

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code

    async def send_text(self, data: str) -> None:
        self.sent_messages.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def send_json(self, data: Any, mode: str = "text") -> None:
        self.sent_messages.append(data)

    async def receive_text(self) -> str:
        if self._incoming_idx >= len(self._incoming):
            # Simulate disconnect
            raise Exception("WebSocket disconnected")
        msg = self._incoming[self._incoming_idx]
        self._incoming_idx += 1
        if isinstance(msg, dict):
            return json.dumps(msg)
        if isinstance(msg, bytes):
            return msg.decode("utf-8")
        return str(msg)

    async def receive_bytes(self) -> bytes:
        if self._incoming_idx >= len(self._incoming):
            raise Exception("WebSocket disconnected")
        msg = self._incoming[self._incoming_idx]
        self._incoming_idx += 1
        if isinstance(msg, str):
            return msg.encode("utf-8")
        if isinstance(msg, dict):
            return json.dumps(msg).encode("utf-8")
        return bytes(msg)

    async def receive_json(self, mode: str = "text") -> Any:
        raw = await self.receive_text()
        return json.loads(raw)

    def enqueue(self, *messages: str | bytes | dict) -> None:
        """Add messages to the incoming queue (for test setup)."""
        self._incoming.extend(messages)


class FakeWSConnection:
    """Stub for an outgoing WebSocket client connection (e.g., websockets.connect).

    For testing code that connects to external WebSocket servers.
    """

    def __init__(self, *, responses: list[str | bytes] | None = None) -> None:
        self._responses: list[str | bytes] = list(responses or [])
        self._response_idx: int = 0
        self.sent: list[str | bytes] = []
        self.closed: bool = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if self._response_idx >= len(self._responses):
            raise ConnectionError("No more responses")
        msg = self._responses[self._response_idx]
        self._response_idx += 1
        return msg

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeWSConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
