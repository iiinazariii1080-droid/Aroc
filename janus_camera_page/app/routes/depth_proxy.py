"""Reverse-proxy routes for the depth camera (192.168.1.55).

Mounted **only** when this instance is ``color_camera``.  The depth camera
sits behind an isolated WiFi router with no internet access, so all browser
traffic for the depth stream must go through the color camera host.

Routes exposed under ``/api/v1/depth_camera/…`` mirror the endpoints the depth
camera serves on its own :8900 — Janus HTTP/WS proxy, client-config, static
assets, HTML, snapshots, depth queries, player scripts, etc.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Dict

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect
from websockets.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.core.settings import get_settings
from app.services import depth_camera_proxy

router = APIRouter(prefix="/api/v1/depth_camera", tags=["depth-camera-proxy"])
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP proxy — catch-all for every depth-camera endpoint
# ---------------------------------------------------------------------------

# Specific routes first (so FastAPI matches them before the catch-all).


@router.api_route(
    "/janus",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="HTTP proxy → depth camera Janus API",
)
async def proxy_depth_janus(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/janus")


@router.get("/client-config", summary="ICE config from depth camera")
async def proxy_depth_client_config(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/client-config")


@router.get("/janus/nat", summary="NAT config from depth camera")
async def proxy_depth_nat(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/janus/nat")


@router.get("/janus/healthz", summary="Janus health from depth camera")
async def proxy_depth_janus_healthz(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/janus/healthz")


@router.get("/healthz", summary="Health probe for depth camera")
async def proxy_depth_healthz(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/healthz")


@router.get("/snapshot.jpg", summary="Snapshot from depth camera")
async def proxy_depth_snapshot(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/snapshot.jpg")


@router.get("/modes", summary="Camera modes from depth camera")
async def proxy_depth_modes(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/modes")


@router.get("/config", summary="Camera config from depth camera")
async def proxy_depth_config_get(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/config")


@router.post("/config", summary="Update camera config on depth camera")
async def proxy_depth_config_post(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/config")


@router.get("/depth", summary="Depth query via depth camera")
async def proxy_depth_depth(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/depth")


@router.get("/depth/frame", summary="Depth frame from depth camera")
async def proxy_depth_depth_frame(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/depth/frame")


@router.get("/depth/frame_color_overlay", summary="Depth frame overlay from depth camera")
async def proxy_depth_depth_overlay(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/depth/frame_color_overlay")


# Static assets & HTML served by the depth camera.

@router.get("/janus.js", include_in_schema=False)
async def proxy_depth_janus_js(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/janus.js")


@router.get("/streamer.js", include_in_schema=False)
async def proxy_depth_streamer_js(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/streamer.js")


@router.get("/gamepaddriver.js", include_in_schema=False)
async def proxy_depth_gamepad_js(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/gamepaddriver.js")


@router.get("/gamepad_config.json", include_in_schema=False)
async def proxy_depth_gamepad_config(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/gamepad_config.json")


@router.get("/depth_features.js", include_in_schema=False)
async def proxy_depth_features_js(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/depth_features.js")


@router.get("/color_view.html", include_in_schema=False)
async def proxy_depth_color_view(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/color_view.html")


@router.get("/depth_view.html", include_in_schema=False)
async def proxy_depth_view(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/depth_view.html")


@router.get("/player/{path:path}", include_in_schema=False)
async def proxy_depth_player_script(path: str, request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, f"/player/{path}")


@router.get("/favicon.ico", include_in_schema=False)
async def proxy_depth_favicon(request: Request) -> Response:
    return await depth_camera_proxy.forward_request(request, "/favicon.ico")


# ---------------------------------------------------------------------------
# WebSocket proxy → depth camera Janus WS
# ---------------------------------------------------------------------------

def _depth_ws_url() -> str:
    """Compute the upstream WS URL for the depth camera's Janus."""
    settings = get_settings()
    base = settings.depth_camera_url.rstrip("/")
    # Convert http→ws, https→wss
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = "ws://" + base
    return f"{ws_base}/janus-ws"


def _ssl_ctx_for(url: str) -> ssl.SSLContext | None:
    if url.startswith("wss://"):
        settings = get_settings()
        ctx = ssl.create_default_context()
        if settings.allow_insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


async def _pump_client_to_upstream(client_ws: WebSocket, upstream_ws) -> None:
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


async def _pump_upstream_to_client(client_ws: WebSocket, upstream_ws) -> None:
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


@router.websocket("/janus-ws")
async def depth_janus_ws_proxy(client_ws: WebSocket) -> None:
    """Bidirectional WS proxy: browser ↔ depth camera Janus."""
    upstream_url = _depth_ws_url()

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
        "ssl": _ssl_ctx_for(upstream_url),
    }
    if subprotocol:
        kwargs["subprotocols"] = [subprotocol]

    try:
        async with ws_connect(upstream_url, **kwargs) as upstream_ws:
            await asyncio.gather(
                _pump_client_to_upstream(client_ws, upstream_ws),
                _pump_upstream_to_client(client_ws, upstream_ws),
            )
    except Exception as exc:
        log.error("Depth camera WS proxy error: %s", exc)
        try:
            await client_ws.close()
        except Exception:
            pass


@router.websocket("/janus/ws")
async def depth_janus_ws_proxy_alt(client_ws: WebSocket) -> None:
    """Alternate path — some clients use /janus/ws instead of /janus-ws."""
    await depth_janus_ws_proxy(client_ws)
