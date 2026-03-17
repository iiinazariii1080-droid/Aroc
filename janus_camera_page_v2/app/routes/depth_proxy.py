"""Reverse-proxy routes for the depth camera (192.168.1.55).

Mounted **only** when this instance is ``color_camera``.  The depth camera
sits behind an isolated WiFi router with no internet access, so all browser
traffic for the depth stream must go through the color camera host.

Routes exposed under ``/api/v1/depth_camera/…`` mirror the endpoints the depth
camera serves on its own :8900 — Janus HTTP/WS proxy, client-config, static
assets, HTML, snapshots, depth queries, player scripts, etc.

HTTP endpoints use a single catch-all handler that forwards any path/method
to the upstream depth camera.  Only WebSocket endpoints require explicit
handlers because FastAPI does not support catch-all WebSocket routes.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response

from fastapi import HTTPException

from app.core.settings import get_settings
from app.services import depth_camera_proxy
from app.services.ws_pump import make_ssl_context, proxy_websocket

router = APIRouter(prefix="/api/v1/depth_camera", tags=["depth-camera-proxy"])
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP proxy — catch-all for every depth-camera endpoint
# ---------------------------------------------------------------------------

# Deny-by-default: only paths matching these prefixes or extensions are
# forwarded without admin auth.  Any unknown path requires admin credentials,
# so newly added privileged endpoints on the depth camera are protected
# automatically.
_PUBLIC_PATH_PREFIXES = (
    "/janus", "/client-config", "/snapshot", "/healthz",
    "/color_view.html", "/depth_view.html", "/ir_view.html",
    "/depth", "/depth_map", "/health/", "/fdir/",
    "/favicon.ico", "/player/", "/streamer.js",
    "/gamepaddriver.js", "/gripper_reticle.js", "/depth_features.js",
)
_PUBLIC_EXTENSIONS = frozenset({
    ".js", ".css", ".html", ".ico", ".png", ".jpg", ".json",
    ".woff", ".woff2",
})


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="HTTP proxy → depth camera",
    description="Forwards any HTTP request to the upstream depth camera at 192.168.1.55:8900.",
)
async def proxy_depth_catchall(path: str, request: Request) -> Response:
    # Deny-by-default: only explicitly public paths are forwarded without
    # admin auth.  Unknown paths require credentials, preventing the proxy
    # from being an unauthenticated backdoor to new depth camera endpoints.
    normalized = f"/{path}"
    is_public = (
        any(normalized.startswith(p) for p in _PUBLIC_PATH_PREFIXES)
        or any(normalized.endswith(ext) for ext in _PUBLIC_EXTENSIONS)
    )
    if not is_public:
        from app.core.admin import require_admin
        await require_admin(request)

    # Guard against oversized request bodies that could OOM an embedded node.
    # Enforced on the actual body, not just Content-Length header, to prevent
    # bypass via chunked transfer encoding or malformed headers.
    _MAX_PROXY_BODY_BYTES = 10 * 1024 * 1024  # 10 MB
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_PROXY_BODY_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Request body too large (limit {_MAX_PROXY_BODY_BYTES // (1024 * 1024)} MB)",
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    # Also enforce on actual body for chunked requests that bypass Content-Length
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        if len(body) > _MAX_PROXY_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Request body too large (limit {_MAX_PROXY_BODY_BYTES // (1024 * 1024)} MB)",
            )

    return await depth_camera_proxy.forward_request(request, normalized)


# ---------------------------------------------------------------------------
# WebSocket proxy → depth camera Janus WS
# ---------------------------------------------------------------------------

def _depth_ws_url() -> str:
    """Compute the upstream WS URL for the depth camera's Janus."""
    settings = get_settings()
    base = settings.depth_cam_url.rstrip("/")
    # Convert http→ws, https→wss
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = "ws://" + base
    return ws_base + settings.depth_cam_janus_ws_path


@router.websocket("/janus-ws")
async def depth_janus_ws_proxy(client_ws: WebSocket) -> None:
    """Bidirectional WS proxy: browser ↔ depth camera Janus."""
    upstream_url = _depth_ws_url()
    settings = get_settings()
    ssl_ctx = make_ssl_context(upstream_url, allow_insecure=settings.allow_insecure_tls)
    await proxy_websocket(client_ws, upstream_url, ssl_ctx=ssl_ctx, log_name="depth-janus-ws-proxy")


@router.websocket("/janus/ws")
async def depth_janus_ws_proxy_alt(client_ws: WebSocket) -> None:
    """Alternate path — some clients use /janus/ws instead of /janus-ws.

    Deprecated: prefer /janus-ws. This path will be removed in a future release.
    """
    log.warning("Deprecated WebSocket path /janus/ws used — migrate to /janus-ws")
    await depth_janus_ws_proxy(client_ws)


# ---------------------------------------------------------------------------
# Depth map load — proxied to the remote depth camera
# ---------------------------------------------------------------------------

_DEPTH_MAP_FORMAT_WHITELIST = frozenset({"json", "raw"})

_depth_map_load_description = (
    "Returns the full depth frame (float32, metres) from the D435 depth sensor. "
    "On a color_camera node this proxies to the remote depth camera."
)


@router.get(
    "/depth_map/load",
    summary="Load full depth map (via depth camera proxy)",
    description=_depth_map_load_description,
)
async def depth_map_load(request: Request, format: str = "json") -> Response:
    """Proxy depth-map request to the remote depth camera."""
    if format not in _DEPTH_MAP_FORMAT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format '{format}'. Must be one of: {sorted(_DEPTH_MAP_FORMAT_WHITELIST)}",
        )
    return await depth_camera_proxy.forward_request(request, "/depth_map/load")
