"""Depth camera routes — only registered when CAM_TYPE == "depth_camera".

Proxies depth queries and frame data to the local realsense_mux (port 8000).
Also provides the depth_map/load endpoint used by arm3d 3-D scene.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests as _req
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(tags=["depth"])

CAM_TYPE = get_settings().camera_type
_REALSENSE_MUX_BASE = "http://localhost:8000"


class DepthResponse(BaseModel):
    type: str = "depth"
    x: float
    y: float
    depth: float


def _proxy_realsense(upstream_path: str, format: str = "json") -> Response | JSONResponse:
    """Common proxy logic for realsense_mux endpoints."""
    url = f"{_REALSENSE_MUX_BASE}{upstream_path}"
    if format:
        url = f"{url}?format={format}"
    try:
        resp = _req.get(url, timeout=5)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        if format == "raw":
            return Response(
                content=resp.content,
                media_type="application/octet-stream",
                headers={
                    "X-Width": resp.headers.get("X-Width", ""),
                    "X-Height": resp.headers.get("X-Height", ""),
                    "X-Dtype": resp.headers.get("X-Dtype", ""),
                    "X-Timestamp": resp.headers.get("X-Timestamp", ""),
                },
            )
        return JSONResponse(content=resp.json())
    except _req.RequestException as e:
        raise HTTPException(status_code=502, detail=f"realsense_mux proxy error: {e}")


# ── Depth query ──

depth_description = (
    "Returns the depth value at the given normalized coordinates (0..100), "
    "where (0,0) is the lower-left corner and (100,100) is the upper-right."
)


@router.get(
    f"/api/v1/{CAM_TYPE}/depth",
    response_model=DepthResponse,
    summary="Get depth at specified coordinates",
    description=depth_description,
)
@router.get("/depth", response_model=DepthResponse, summary="Get depth at specified coordinates", description=depth_description)
def get_depth(
    x: Optional[float] = None,
    y: Optional[float] = None,
    message: Optional[str] = None,
) -> DepthResponse:
    def parse_from_message(payload: str) -> tuple[Optional[float], Optional[float]]:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return None, None
        try:
            return (
                float(data["x"]) if "x" in data else None,
                float(data["y"]) if "y" in data else None,
            )
        except (TypeError, ValueError):
            return None, None

    if (x is None or y is None) and message:
        msg_x, msg_y = parse_from_message(message)
        x = x if x is not None else msg_x
        y = y if y is not None else msg_y

    if x is None or y is None:
        raise HTTPException(status_code=422, detail="Parameters 'x' and 'y' are required")

    x = max(0.0, min(100.0, x))
    y = max(0.0, min(100.0, y))

    try:
        resp = _req.get(f"{_REALSENSE_MUX_BASE}/depth?x={x}&y={y}", timeout=5)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return DepthResponse(**resp.json())
    except _req.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Depth query error: {e}")


# ── Color frame ──

@router.get(f"/api/v1/{CAM_TYPE}/depth/color_frame", summary="Get D435 colour frame (RGB24)")
@router.get("/depth/color_frame", summary="Get D435 colour frame (RGB24)")
def get_depth_color_frame(format: str = "json"):
    return _proxy_realsense("/color_frame", format)


# ── Depth frame ──

@router.get(f"/api/v1/{CAM_TYPE}/depth/frame", summary="Get full depth frame (float32)")
@router.get("/depth/frame", summary="Get full depth frame (float32)")
def get_depth_frame(format: str = "json"):
    return _proxy_realsense("/depth_map", format)


# ── Aligned RGBD overlay ──

@router.get(f"/api/v1/{CAM_TYPE}/depth/frame_color_overlay", summary="Get aligned RGBD frame")
@router.get("/depth/frame_color_overlay", summary="Get aligned RGBD frame")
def get_depth_frame_color_overlay(format: str = "json"):
    try:
        color_resp = _req.get(f"{_REALSENSE_MUX_BASE}/color_frame?format=json", timeout=5)
        depth_resp = _req.get(f"{_REALSENSE_MUX_BASE}/depth_map?format=json", timeout=5)
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
    except _req.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Aligned RGBD proxy error: {e}")


# ── Depth map load (used by arm3d 3-D scene) ──

@router.get(f"/api/v1/{CAM_TYPE}/depth_map/load", include_in_schema=False)
@router.get("/api/v1/depth_map/load", summary="Load full depth map")
@router.get("/depth_map/load", include_in_schema=False)
def depth_map_load(format: str = "json"):
    settings = get_settings()
    if settings.camera_type == "depth_camera":
        upstream = f"{_REALSENSE_MUX_BASE}/depth_map?format={format}"
    else:
        upstream = f"{settings.depth_cam_url}/depth_map/load?format={format}"
    try:
        resp = _req.get(upstream, timeout=5)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        if format == "raw":
            return Response(
                content=resp.content,
                media_type="application/octet-stream",
                headers={
                    "X-Width": resp.headers.get("X-Width", ""),
                    "X-Height": resp.headers.get("X-Height", ""),
                    "X-Dtype": resp.headers.get("X-Dtype", "float32"),
                    "X-Timestamp": resp.headers.get("X-Timestamp", ""),
                },
            )
        return JSONResponse(content=resp.json())
    except _req.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Depth map proxy error: {exc}")
