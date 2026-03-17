"""Shared WebSocket pump utilities for bidirectional proxy connections.

Used by app/routes/janus.py and app/routes/depth_proxy.py to avoid
duplicating the pump logic between WebSocket proxy endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Dict, List, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

logger = logging.getLogger(__name__)


def make_ssl_context(url: str, allow_insecure: bool = False) -> Optional[ssl.SSLContext]:
    """Return an SSLContext for WSS connections, or None for plain WS.

    When *allow_insecure* is True, certificate verification is disabled —
    this must never be used in production (guarded by ALLOW_INSECURE_TLS env).
    """
    if not url.startswith("wss://"):
        return None
    ctx = ssl.create_default_context()
    if allow_insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def proxy_websocket(
    client_ws: WebSocket,
    upstream_url: str,
    ssl_ctx: Optional[ssl.SSLContext] = None,
    subprotocols: Optional[List[str]] = None,
    log_name: str = "ws-proxy",
) -> None:
    """Accept *client_ws* and bidirectionally proxy it to *upstream_url*.

    Handles subprotocol negotiation, connection teardown, and error logging.
    """
    offered_header = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [p.strip() for p in offered_header.split(",") if p.strip()]
    sub = "janus-protocol" if "janus-protocol" in offered else None

    await client_ws.accept(subprotocol=sub)

    kwargs: Dict[str, object] = {
        "open_timeout": 5,
        "ping_interval": 10,
        "ping_timeout": 10,
        "close_timeout": 3,
        "max_size": 2 ** 20,
        "compression": None,
        "ssl": ssl_ctx,
    }
    # Janus requires Sec-WebSocket-Protocol: janus-protocol in the upgrade
    # request and echoes it back — so we MUST pass subprotocols to ws_connect.
    if sub:
        kwargs["subprotocols"] = [sub]
    if subprotocols:  # caller override
        kwargs["subprotocols"] = subprotocols

    try:
        async with ws_connect(upstream_url, **kwargs) as upstream_ws:
            await asyncio.gather(
                pump_client_to_upstream(client_ws, upstream_ws),
                pump_upstream_to_client(client_ws, upstream_ws),
            )
    except Exception as exc:
        logger.error("%s error: %s", log_name, exc)
        try:
            await client_ws.close()
        except Exception:
            pass


async def pump_client_to_upstream(client_ws: WebSocket, upstream_ws) -> None:
    """Forward messages from the browser client to the upstream WebSocket."""
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
                    pass
                break
    except WebSocketDisconnect:
        try:
            await upstream_ws.close()
        except Exception:
            pass


async def pump_upstream_to_client(client_ws: WebSocket, upstream_ws) -> None:
    """Forward messages from the upstream WebSocket to the browser client."""
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
            pass
