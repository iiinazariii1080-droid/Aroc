"""Shared bidirectional WebSocket proxy helpers.

Used by both the Janus WS proxy (color camera) and the depth camera WS proxy
to avoid duplicating the pump logic.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Dict

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.core.settings import get_settings

log = logging.getLogger(__name__)


def ssl_ctx_for(url: str) -> ssl.SSLContext | None:
    """Build an SSL context for wss:// URLs, or return None for ws://."""
    if url.startswith("wss://"):
        settings = get_settings()
        ctx = ssl.create_default_context()
        if settings.allow_insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


async def pump_client_to_upstream(client_ws: WebSocket, upstream_ws) -> None:
    """Forward messages from browser WebSocket to upstream backend."""
    try:
        while True:
            message = await client_ws.receive()
            msg_type = message.get("type")
            if msg_type == "websocket.receive":
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

    Args:
        client_ws: Incoming browser WebSocket.
        upstream_url: Backend ws:// or wss:// URL.
        pass_subprotocol: If True, forward janus-protocol subprotocol to upstream.
        label: Log label for error messages.
    """
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
            await asyncio.gather(
                pump_client_to_upstream(client_ws, upstream_ws),
                pump_upstream_to_client(client_ws, upstream_ws),
            )
    except Exception as exc:
        log.error("%s error [url=%s]: %s", label, upstream_url, exc, exc_info=True)
        try:
            await client_ws.close()
        except Exception:
            log.debug("client close failed during error cleanup", exc_info=True)
