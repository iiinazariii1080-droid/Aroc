"""Depth camera routes — registered for both camera types (depth_map/load is universal).

Proxies depth queries and frame data to the local realsense_mux (port 8000).
Also provides the depth_map/load endpoint used by arm3d 3-D scene.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(tags=["depth"])

# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type

# Async HTTP client for realsense_mux — lazy-initialized on first use.
_mux_client: httpx.AsyncClient | None = None


async def _get_mux_client() -> httpx.AsyncClient:
    global _mux_client
    if _mux_client is None:
        _mux_client = httpx.AsyncClient(
            base_url=get_settings().realsense_mux_url,
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=5.0),
        )
    return _mux_client


async def close_mux_client() -> None:
    """Close the realsense_mux HTTP client (called on app shutdown)."""
    global _mux_client
    if _mux_client is not None:
        await _mux_client.aclose()
        _mux_client = None


class DepthResponse(BaseModel):
    type: str = "depth"
    x: float
    y: float
    depth: float


async def _proxy_realsense(upstream_path: str, format: str = "json") -> Response | JSONResponse:
    """Common proxy logic for realsense_mux endpoints."""
    client = await _get_mux_client()
    params = {"format": format} if format else {}
    try:
        resp = await client.get(upstream_path, params=params)
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
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"realsense_mux timeout: {e}")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"realsense_mux unreachable: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"realsense_mux proxy error: {e}")


# ── Depth query ──

depth_description = (
    "Returns the depth value at the given normalized coordinates (0..100), "
    "where (0,0) is the lower-left corner and (100,100) is the upper-right."
)


@router.get(
    f"/api/v1/{_CAM_TYPE}/depth",
    response_model=DepthResponse,
    summary="Get depth at specified coordinates",
    description=depth_description,
)
@router.get("/depth", response_model=DepthResponse, summary="Get depth at specified coordinates", description=depth_description)
async def get_depth(
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

    client = await _get_mux_client()
    try:
        resp = await client.get("/depth", params={"x": x, "y": y})
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return DepthResponse(**resp.json())
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Depth query timeout: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Depth query error: {e}")


# ── Color frame ──

@router.get(f"/api/v1/{_CAM_TYPE}/depth/color_frame", summary="Get D435 colour frame (RGB24)")
@router.get("/depth/color_frame", summary="Get D435 colour frame (RGB24)")
async def get_depth_color_frame(format: str = "json"):
    return await _proxy_realsense("/color_frame", format)


# ── Depth frame ──

@router.get(f"/api/v1/{_CAM_TYPE}/depth/frame", summary="Get full depth frame (float32)")
@router.get("/depth/frame", summary="Get full depth frame (float32)")
async def get_depth_frame(format: str = "json"):
    return await _proxy_realsense("/depth_map", format)


# ── Aligned RGBD overlay ──

@router.get(f"/api/v1/{_CAM_TYPE}/depth/frame_color_overlay", summary="Get aligned RGBD frame")
@router.get("/depth/frame_color_overlay", summary="Get aligned RGBD frame")
async def get_depth_frame_color_overlay(format: str = "json"):
    client = await _get_mux_client()
    try:
        color_resp, depth_resp = await asyncio.gather(
            client.get("/color_frame", params={"format": "json"}),
            client.get("/depth_map", params={"format": "json"}),
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
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail=f"Aligned RGBD timeout: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Aligned RGBD proxy error: {e}")


# ── Depth map load (used by arm3d 3-D scene) ──

@router.get(f"/api/v1/{_CAM_TYPE}/depth_map/load", include_in_schema=False)
@router.get("/api/v1/depth_map/load", summary="Load full depth map")
@router.get("/depth_map/load", include_in_schema=False)
async def depth_map_load(format: str = "json"):
    settings = get_settings()
    if settings.camera_type == "depth_camera":
        return await _proxy_realsense("/depth_map", format)

    # Color camera → proxy to depth camera node (reuses managed client pool)
    from app.services import depth_camera_proxy
    try:
        resp = await depth_camera_proxy.get("/depth_map/load", format=format)
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
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Depth map timeout: {exc}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Depth map proxy error: {exc}")
