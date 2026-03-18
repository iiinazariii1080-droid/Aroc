"""Reverse-proxy routes for the depth camera (192.168.1.55).

Mounted **only** when this instance is ``color_camera``.  The depth camera
sits behind an isolated WiFi router with no internet access, so all browser
traffic for the depth stream must go through the color camera host.

Routes exposed under ``/api/v1/depth_camera/…`` mirror the endpoints the depth
camera serves on its own :8900 — Janus HTTP/WS proxy, client-config, static
assets, HTML, snapshots, depth queries, player scripts, etc.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response

from app.core.settings import get_settings
from app.services import depth_camera_proxy

router = APIRouter(prefix="/api/v1/depth_camera", tags=["depth-camera-proxy"])
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whitelist of upstream paths that may be proxied.
# Anything not matching is rejected with 404 — prevents open-relay abuse.
# ---------------------------------------------------------------------------

_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/janus",
    "/client-config",
    "/janus/nat",
    "/janus/healthz",
    "/healthz",
    "/snapshot.jpg",
    "/modes",
    "/config",
    "/depth",
    "/depth/frame",
    "/depth/color_frame",
    "/depth/frame_color_overlay",
    "/depth_map/load",
    "/janus.js",
    "/streamer.js",
    "/gamepaddriver.js",
    "/gamepad_config.json",
    "/depth_features.js",
    "/gripper_reticle.js",
    "/color_view.html",
    "/depth_view.html",
    "/ir_view.html",
    "/favicon.ico",
    "/status",
    "/health/stream",
    "/metrics",
})

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/player/",
    "/static/",
)

_MULTI_METHOD_PATHS: frozenset[str] = frozenset({
    "/janus",
    "/config",
})


def _is_allowed(upstream_path: str) -> bool:
    if upstream_path in _ALLOWED_PATHS:
        return True
    return any(upstream_path.startswith(p) for p in _ALLOWED_PREFIXES)


# ---------------------------------------------------------------------------
# HTTP proxy — catch-all with whitelist
# ---------------------------------------------------------------------------

@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="HTTP proxy → depth camera (whitelist-gated)",
    include_in_schema=False,
)
async def proxy_depth_catchall(path: str, request: Request) -> Response:
    upstream_path = f"/{path}" if path else "/"
    if not _is_allowed(upstream_path):
        raise HTTPException(status_code=404, detail="Not found")
    if request.method != "GET" and upstream_path not in _MULTI_METHOD_PATHS:
        raise HTTPException(status_code=405, detail="Method not allowed")
    return await depth_camera_proxy.forward_request(request, upstream_path)


# ---------------------------------------------------------------------------
# WebSocket proxy → depth camera Janus WS
# ---------------------------------------------------------------------------

def _depth_ws_url() -> str:
    """Compute the upstream WS URL for the depth camera's Janus."""
    base = get_settings().depth_cam_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = "ws://" + base
    return f"{ws_base}/janus-ws"


@router.websocket("/janus-ws")
async def depth_janus_ws_proxy(client_ws: WebSocket) -> None:
    """Bidirectional WS proxy: browser <-> depth camera Janus."""
    from app.services.ws_proxy import proxy_websocket

    await proxy_websocket(
        client_ws,
        _depth_ws_url(),
        pass_subprotocol=True,
        label="depth-janus-ws",
    )


@router.websocket("/janus/ws")
async def depth_janus_ws_proxy_alt(client_ws: WebSocket) -> None:
    """Alternate path — some clients use /janus/ws instead of /janus-ws."""
    await depth_janus_ws_proxy(client_ws)
