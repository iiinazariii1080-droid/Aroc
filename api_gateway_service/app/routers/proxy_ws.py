from contextlib import suppress
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # compatibility with older websockets versions
    from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError, WebSocketException

from app.core.config import (
    WS_XARM_URL,
    WS_COLOR_CAMERA_URL,
    WS_DEPTH_CAMERA_URL,
    WS_ACQUIRE_TIMEOUT_S,
    WS_CONNECT_TIMEOUT_S,
    WS_TOTAL_TIMEOUT_S,
)
from app.core.circuit_breaker import get_breaker
from app.core.http_client import get_ws_semaphore
from app.core.tls_utils import ssl_ctx_for

router = APIRouter(prefix="/api/v1", tags=["WebSocket Proxy"])
logger = logging.getLogger(__name__)

_WS_TARGETS = {
    "xarm": WS_XARM_URL,
    "color_camera": WS_COLOR_CAMERA_URL,
    "depth_camera": WS_DEPTH_CAMERA_URL,
}


async def _pump_client_to_upstream(client_ws: WebSocket, upstream_ws):
    """Forward messages from the browser client to the upstream WS server."""
    try:
        while True:
            msg = await client_ws.receive()
            t = msg.get("type")
            if t == "websocket.receive":
                if "text" in msg and msg["text"] is not None:
                    await upstream_ws.send(msg["text"])
                elif "bytes" in msg and msg["bytes"] is not None:
                    await upstream_ws.send(msg["bytes"])
            elif t == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        with suppress(Exception):
            await upstream_ws.close()


async def _pump_upstream_to_client(client_ws: WebSocket, upstream_ws):
    """Forward messages from the upstream WS server to the browser client."""
    try:
        async for message in upstream_ws:
            if isinstance(message, (bytes, bytearray)):
                await client_ws.send_bytes(message)
            else:
                await client_ws.send_text(message)
    except (ConnectionClosedOK, ConnectionClosedError):
        pass
    finally:
        with suppress(Exception):
            await client_ws.close()


async def _janus_ws_proxy(client_ws: WebSocket, target: str):
    query_params = client_ws.url.query
    backend_url = _WS_TARGETS.get(target)
    if not backend_url:
        raise ValueError(f"Unsupported WS target: {target}")
    if not backend_url.startswith(("ws://", "wss://")):
        backend_url = f"ws://{backend_url}"
    target_url = f"{backend_url}?{query_params}" if query_params else backend_url

    # ── Circuit breaker: reject immediately if service is down ──
    cb = get_breaker(target)
    if not cb.allow_request():
        logger.debug("WS CIRCUIT_OPEN for %s", target)
        await client_ws.accept()
        await client_ws.close(code=1013, reason="Service temporarily unavailable")
        return

    # ── WebSocket concurrency guard (wait up to N seconds for a slot) ──
    ws_sem = get_ws_semaphore(client_ws.app)
    try:
        await asyncio.wait_for(ws_sem.acquire(), timeout=WS_ACQUIRE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("WS concurrency limit reached after %.1fs wait, rejecting %s", WS_ACQUIRE_TIMEOUT_S, target)
        await client_ws.accept()
        await client_ws.close(code=1013, reason="Too many WebSocket connections")
        return
    # Semaphore acquired — release in finally block below

    # Capture subprotocols offered by the client (Janus.js usually sends janus-protocol)
    req_hdr = client_ws.headers.get("sec-websocket-protocol", "")
    offered = [s.strip() for s in req_hdr.split(",") if s.strip()]
    use_sub = "janus-protocol" if "janus-protocol" in offered else None

    # Accept using the same subprotocol (or none if the client did not offer one)
    await client_ws.accept(subprotocol=use_sub)

    kwargs = dict(
        open_timeout=WS_CONNECT_TIMEOUT_S,
        ping_interval=20, ping_timeout=20, close_timeout=5,
        max_size=2**20, compression=None,
        ssl=ssl_ctx_for(backend_url),
    )
    if use_sub:
        kwargs["subprotocols"] = [use_sub]

    try:
        try:
            async with asyncio.timeout(WS_TOTAL_TIMEOUT_S):
                async with ws_connect(target_url, **kwargs) as upstream_ws:
                    cb.record_success()
                    logger.info("WS connected to %s", target_url)
                    t1 = asyncio.create_task(_pump_client_to_upstream(client_ws, upstream_ws))
                    t2 = asyncio.create_task(_pump_upstream_to_client(client_ws, upstream_ws))
                    done, pending = await asyncio.wait(
                        {t1, t2}, return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        with suppress(asyncio.CancelledError):
                            await t
                    for t in done:
                        if t.exception():
                            raise t.exception()
        except TimeoutError:
            cb.record_failure()
            logger.warning("WS session timeout (%ss) for %s", WS_TOTAL_TIMEOUT_S, target_url)
            with suppress(Exception):
                await client_ws.close(code=1000, reason="Session timeout")
        except (OSError, ConnectionRefusedError, WebSocketException, RuntimeError) as exc:
            cb.record_failure()
            logger.error("WS connect failed (%s): %s", target_url, exc)
            with suppress(Exception):
                await client_ws.close(code=1011, reason="Upstream connection failed")
    finally:
        ws_sem.release()


@router.websocket("/color_camera/janus-ws")
async def color_camera_janus_ws(client_ws: WebSocket):
    await _janus_ws_proxy(client_ws, "color_camera")


@router.websocket("/depth_camera/janus-ws")
async def depth_camera_janus_ws(client_ws: WebSocket):
    await _janus_ws_proxy(client_ws, "depth_camera")


@router.websocket("/xarm/ws")
async def xarm_ws_proxy(client_ws: WebSocket):
    await _janus_ws_proxy(client_ws, "xarm")
