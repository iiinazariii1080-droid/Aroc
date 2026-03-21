"""Shared bidirectional WebSocket proxy helpers.

Used by both the Janus WS proxy (color camera) and the depth camera WS proxy
to avoid duplicating the pump logic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import time
from typing import Dict

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.core.settings import get_settings

log = logging.getLogger(__name__)

# ── WebSocket connection / rate limits ───────────────────────────────
_MAX_WS_CONNECTIONS = int(os.getenv("WS_MAX_CONNECTIONS", "10"))
_WS_MSG_RATE_PER_SEC = float(os.getenv("WS_MSG_RATE_PER_SEC", "60"))
# Idle timeout: close client connection if no message received within this window.
# Prevents semaphore exhaustion by idle/malicious clients (DEF-02).
_WS_IDLE_TIMEOUT_SEC = float(os.getenv("WS_IDLE_TIMEOUT_SEC", "120"))
_ws_semaphore = asyncio.Semaphore(_MAX_WS_CONNECTIONS)
_ws_active: int = 0
_ws_active_lock = asyncio.Lock()


def ssl_ctx_for(url: str) -> ssl.SSLContext | None:
    """Build an SSL context for wss:// URLs, or return None for ws://."""
    if url.startswith("wss://"):
        return ssl.create_default_context()
    return None


async def pump_client_to_upstream(
    client_ws: WebSocket,
    upstream_ws,
    *,
    rate_limit: float = 0,
) -> None:
    """Forward messages from browser WebSocket to upstream backend.

    Args:
        rate_limit: Max messages/sec (0 = unlimited).
    """
    msg_count = 0
    window_start = time.monotonic()

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    client_ws.receive(), timeout=_WS_IDLE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                log.warning("WS client idle for %.0fs, closing", _WS_IDLE_TIMEOUT_SEC)
                try:
                    await upstream_ws.close()
                except Exception:
                    pass
                break
            msg_type = message.get("type")
            if msg_type == "websocket.receive":
                # Per-connection rate limiting
                if rate_limit > 0:
                    now = time.monotonic()
                    elapsed = now - window_start
                    if elapsed >= 1.0:
                        msg_count = 0
                        window_start = now
                    msg_count += 1
                    if msg_count > rate_limit:
                        log.warning("WS message rate exceeded (%d/s), dropping", msg_count)
                        continue

                if "text" in message and message["text"] is not None:
                    await upstream_ws.send(message["text"])
                elif "bytes" in message and message["bytes"] is not None:
                    await upstream_ws.send(message["bytes"])
            elif msg_type == "websocket.disconnect":
                try:
                    await upstream_ws.close()
                except Exception:
                    log.debug("upstream close failed on client disconnect", exc_info=True)
                break
    except WebSocketDisconnect:
        try:
            await upstream_ws.close()
        except Exception:
            log.debug("upstream close failed on WebSocketDisconnect", exc_info=True)


async def pump_upstream_to_client(client_ws: WebSocket, upstream_ws) -> None:
    """Forward messages from upstream backend to browser WebSocket."""
    try:
        async for message in upstream_ws:
            if isinstance(message, (bytes, bytearray)):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except (ConnectionClosedOK, ConnectionClosedError):
        try:
            await client_ws.close()
        except Exception:
            log.debug("client close failed on upstream disconnect", exc_info=True)


async def proxy_websocket(
    client_ws: WebSocket,
    upstream_url: str,
    *,
    pass_subprotocol: bool = False,
    label: str = "ws-proxy",
) -> None:
    """Full bidirectional WS proxy: accept client, connect upstream, pump both directions.

    Enforces a maximum number of concurrent WebSocket connections and
    per-connection message rate limiting to prevent resource exhaustion.

    Args:
        client_ws: Incoming browser WebSocket.
        upstream_url: Backend ws:// or wss:// URL.
        pass_subprotocol: If True, forward janus-protocol subprotocol to upstream.
        label: Log label for error messages.
    """
    global _ws_active

    # Atomic: semaphore IS the gate — no separate pre-check (fixes TOCTOU DEF-05)
    try:
        await asyncio.wait_for(_ws_semaphore.acquire(), timeout=0.05)
    except (asyncio.TimeoutError, Exception):
        log.warning("WS connection limit reached (%d), rejecting", _MAX_WS_CONNECTIONS)
        try:
            await client_ws.close(code=1013)  # Try Again Later
        except Exception:
            pass
        return

    try:
        async with _ws_active_lock:
            _ws_active += 1
        try:
            await _proxy_websocket_inner(client_ws, upstream_url,
                                         pass_subprotocol=pass_subprotocol,
                                         label=label)
        finally:
            async with _ws_active_lock:
                _ws_active -= 1
    finally:
        _ws_semaphore.release()


def ws_active_connections() -> int:
    """Return number of currently active WebSocket proxy connections."""
    return _ws_active


def _validate_ws_origin(client_ws: WebSocket) -> bool:
    """Validate WebSocket Origin header against CORS origin regex.

    Returns True if origin is allowed or absent (same-origin requests
    omit Origin).  Returns False if origin is present but not allowed.
    """
    origin = client_ws.headers.get("origin")
    if origin is None:
        return True
    return bool(re.match(get_settings().cors_origin_regex, origin))


async def _proxy_websocket_inner(
    client_ws: WebSocket,
    upstream_url: str,
    *,
    pass_subprotocol: bool = False,
    label: str = "ws-proxy",
) -> None:
    """Inner proxy logic (called under semaphore guard)."""
    # Origin validation (DEF-02: defence-in-depth for WebSocket endpoints)
    if not _validate_ws_origin(client_ws):
        log.warning("%s rejected: origin %r not in allowlist", label, client_ws.headers.get("origin"))
        await client_ws.close(code=1008)  # Policy Violation
        return

    req_header = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [item.strip() for item in req_header.split(",") if item.strip()]
    subprotocol = "janus-protocol" if "janus-protocol" in offered else None

    await client_ws.accept(subprotocol=subprotocol)

    kwargs: Dict[str, object] = {
        "open_timeout": 5,
        "ping_interval": 10,
        "ping_timeout": 10,
        "close_timeout": 3,
        "max_size": 2**20,
        "compression": None,
        "ssl": ssl_ctx_for(upstream_url),
    }
    if pass_subprotocol and subprotocol:
        kwargs["subprotocols"] = [subprotocol]

    log.info("%s upstream: %s sub=%s", label, upstream_url, subprotocol)
    try:
        async with ws_connect(upstream_url, **kwargs) as upstream_ws:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(pump_client_to_upstream(
                    client_ws, upstream_ws, rate_limit=_WS_MSG_RATE_PER_SEC))
                tg.create_task(pump_upstream_to_client(client_ws, upstream_ws))
    except Exception as exc:
        log.error("%s error [url=%s]: %s", label, upstream_url, exc, exc_info=True)
        try:
            await client_ws.close()
        except Exception:
            log.debug("client close failed during error cleanup", exc_info=True)
