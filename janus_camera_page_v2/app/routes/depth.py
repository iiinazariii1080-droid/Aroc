"""Depth-camera-specific API routes.

This module is only mounted when ``CAM_TYPE == depth_camera``.
It contains all routes that query the local RealSense mux (realsense_mux_url)
for depth, IR, and colour frame data, as well as the depth/IR HTML views.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.core.settings import get_settings
from app.routes.helpers import proxy_frame_response
from app.services import realsense_mux_proxy
from app.utils.templates import get_jinja

router = APIRouter(tags=["depth"])

_CAM_TYPE = "depth_camera"  # this module is only loaded on depth_camera nodes

logger = logging.getLogger(__name__)


# ── Template helpers ─────────────────────────────────────────────────────────

def _render_template(filename: str) -> HTMLResponse:
    try:
        template = get_jinja().get_template(filename)
    except Exception:
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    ctx = {
        "cam_type": _CAM_TYPE,
        "joystick_mode": "off",
    }
    return HTMLResponse(template.render(**ctx))


def _render_color_view_variant(
    stream_id: int,
    stream_name: str,
    joystick: bool = True,
    depth_features: bool = False,
) -> HTMLResponse:
    try:
        template = get_jinja().get_template("color_view.html")
    except Exception:
        raise HTTPException(status_code=404, detail="color_view.html not found")
    ctx = {
        "cam_type": _CAM_TYPE,
        "joystick_mode": "always" if joystick else "off",
        "prefer_stream_id": str(stream_id),
        "stream_name": stream_name,
        "depth_features_url": f"/api/v1/{_CAM_TYPE}/depth_features.js" if depth_features else "",
    }
    return HTMLResponse(template.render(**ctx))


# ── Response model ─────────────────────────────────────────────────────────

class DepthResponse(BaseModel):
    type: str = "depth"
    x: float
    y: float
    depth: float


# ── HTML views ─────────────────────────────────────────────────────────────

@router.get(f"/api/v1/{_CAM_TYPE}/depth_view.html", include_in_schema=False)
@router.get("/depth_view.html", include_in_schema=False)
def depth_view() -> HTMLResponse:
    settings = get_settings()
    if (settings.templates_dir / "depth_view.html").exists():
        return _render_template("depth_view.html")
    return _render_color_view_variant(1306, "RealSense Depth", joystick=False, depth_features=True)


@router.get(f"/api/v1/{_CAM_TYPE}/ir_view.html", include_in_schema=False)
@router.get("/ir_view.html", include_in_schema=False)
def ir_view() -> HTMLResponse:
    settings = get_settings()
    if (settings.templates_dir / "ir_view.html").exists():
        return _render_template("ir_view.html")
    return _render_color_view_variant(1307, "RealSense IR", joystick=False)


# ── Depth value at coordinates ─────────────────────────────────────────────

_depth_description = (
    "Returns the depth value at the given normalized coordinates (0..100), "
    "where (0,0) is the lower-left corner of the video frame and (100,100) is the upper-right."
)


def _parse_xy_from_message(payload: str) -> tuple[Optional[float], Optional[float]]:
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None, None
    x_val = data.get("x")
    y_val = data.get("y")
    try:
        return (
            float(x_val) if x_val is not None else None,
            float(y_val) if y_val is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


@router.get(
    f"/api/v1/{_CAM_TYPE}/depth",
    response_model=DepthResponse,
    summary="Get depth at specified coordinates",
    description=_depth_description,
)
@router.get(
    "/depth",
    response_model=DepthResponse,
    summary="Get depth at specified coordinates",
    description=_depth_description,
)
async def get_depth(
    x: Optional[float] = None,
    y: Optional[float] = None,
    message: Optional[str] = None,
) -> DepthResponse:
    if (x is None or y is None) and message:
        msg_x, msg_y = _parse_xy_from_message(message)
        x = x if x is not None else msg_x
        y = y if y is not None else msg_y

    if x is None or y is None:
        raise HTTPException(status_code=422, detail="Parameters 'x' and 'y' are required")

    x = max(0.0, min(100.0, x))
    y = max(0.0, min(100.0, y))

    resp = await realsense_mux_proxy.get("/depth", params={"x": x, "y": y})
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return DepthResponse(**resp.json())


# ── Colour frame ───────────────────────────────────────────────────────────

_color_frame_description = (
    "Returns the latest D435 colour (RGB24) frame from the RealSense color sensor. "
    "Use format=json for base64-encoded JSON payload, or format=raw for raw bytes."
)


@router.get(
    f"/api/v1/{_CAM_TYPE}/depth/color_frame",
    summary="Get real D435 colour frame (RGB24)",
    description=_color_frame_description,
)
@router.get(
    "/depth/color_frame",
    summary="Get real D435 colour frame (RGB24)",
    description=_color_frame_description,
)
async def get_depth_color_frame(format: str = "json"):
    resp = await realsense_mux_proxy.get("/color_frame", params={"format": format})
    return proxy_frame_response(resp, format, default_dtype="uint8-rgb24")


# ── Full depth frame ───────────────────────────────────────────────────────

_depth_frame_description = (
    "Returns the full depth frame (float32, metres) from the D435 depth sensor. "
    "Proxies to the local realsense_mux /depth_map endpoint."
)


@router.get(
    f"/api/v1/{_CAM_TYPE}/depth/frame",
    summary="Get full depth frame",
    description=_depth_frame_description,
)
@router.get(
    "/depth/frame",
    summary="Get full depth frame",
    description=_depth_frame_description,
)
async def get_depth_frame(format: str = "json"):
    resp = await realsense_mux_proxy.get("/depth_map", params={"format": format})
    return proxy_frame_response(resp, format, default_dtype="float32", include_timestamp=True)


# ── Aligned RGBD frame ─────────────────────────────────────────────────────

@router.get(
    f"/api/v1/{_CAM_TYPE}/depth/frame_color_overlay",
    summary="Get aligned RGBD frame",
    description=(
        "Fetches both /color_frame and /depth_map from realsense_mux and combines them "
        "into a single JSON payload for arm3d scene-helpers.js fetchAlignedRgbdJson()."
    ),
)
@router.get("/depth/frame_color_overlay", summary="Get aligned RGBD frame")
async def get_depth_frame_color_overlay(format: str = "json"):
    import asyncio
    color_resp, depth_resp = await asyncio.gather(
        realsense_mux_proxy.get("/color_frame", params={"format": "json"}),
        realsense_mux_proxy.get("/depth_map", params={"format": "json"}),
    )
    if color_resp.status_code != 200:
        raise HTTPException(status_code=color_resp.status_code, detail=color_resp.text)
    if depth_resp.status_code != 200:
        raise HTTPException(status_code=depth_resp.status_code, detail=depth_resp.text)
    cj = color_resp.json()
    dj = depth_resp.json()
    return JSONResponse(content={
        "width": dj["width"],
        "height": dj["height"],
        "timestamp": dj.get("timestamp", 0),
        "rgb_data": cj["data"],
        "rgb_dtype": cj.get("dtype", "uint8-rgb24"),
        "depth_data": dj["data"],
        "depth_dtype": dj.get("dtype", "float32"),
    })


# ── Full depth map (load endpoint) ──────────────────────────────────────────

_DEPTH_MAP_FORMAT_WHITELIST = frozenset({"json", "raw"})

_depth_map_load_description = (
    "Returns the full depth frame (float32, metres) from the D435 depth sensor. "
    "On a depth_camera node this proxies to the local realsense_mux."
)


@router.get(
    "/depth_map/load",
    summary="Load full depth map",
    description=_depth_map_load_description,
)
async def depth_map_load(format: str = "json"):
    """Proxy depth-map request to the local realsense_mux."""
    if format not in _DEPTH_MAP_FORMAT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format '{format}'. Must be one of: {sorted(_DEPTH_MAP_FORMAT_WHITELIST)}",
        )
    resp = await realsense_mux_proxy.get("/depth_map", params={"format": format})
    return proxy_frame_response(resp, format, default_dtype="float32", include_timestamp=True)
