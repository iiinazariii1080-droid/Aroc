from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

# Fallback CDN for janus.js when templates/janus.js is not present (classic browser build from Meetecho repo)
JANUS_JS_CDN_URL = "https://cdn.jsdelivr.net/gh/meetecho/janus-gateway@master/html/janus.js"

from app.core.dependencies import require_api_key
from app.core.settings import get_settings
from app.services import janus
from app.services import relay_proxy
from app.services.env_store import read_env
from app.services.system import service_restart, systemd_brief
from app.services.v4l2 import v4l2_current

router = APIRouter(tags=["system"])
CAM_TYPE = get_settings().camera_type
class HealthResponse(BaseModel):
    ok: bool = Field(..., description="Application is up and serving requests.")

class CameraInfo(BaseModel):
    device: str
    pixel_format: str

class RequestedConfig(BaseModel):
    width: int
    height: int
    fps: int

class EffectiveVideoState(BaseModel):
    width: Optional[int]
    height: Optional[int]
    fps: Optional[int]
    pixfmt: Optional[str]

class FfmpegConfig(BaseModel):
    BITRATE_KBPS: int
    GOP: int
    PRESET: str
    TUNE: str
    PORT: int

class ServiceBrief(BaseModel):
    active: bool
    since: Optional[str]
    restarts: int

class JanusStatus(BaseModel):
    mountpoint_id: Optional[int] = None
    enabled: Optional[bool] = None
    video_active: Optional[bool] = None
    video_age_ms: Optional[int] = None
    codec: Optional[str] = None
    pt: Optional[int] = None
    fmtp: Optional[str] = None
    error: Optional[str] = None

class SystemStatusResponse(BaseModel):
    camera: CameraInfo
    requested: RequestedConfig
    effective: EffectiveVideoState
    ffmpeg: FfmpegConfig
    service: ServiceBrief
    janus: JanusStatus
    last_applied_at: Optional[float]

class ActionResponse(BaseModel):
    ok: bool
    message: str

@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Service health probe",
    description="Used by Kubernetes/NGINX probes or external health checks.",
)
def healthz() -> HealthResponse:
    return HealthResponse(ok=True)


# ── Relay proxy: joystick e2e latency measurement ──

@router.get(f"/api/v1/{CAM_TYPE}/relay/time", include_in_schema=False)
@router.get("/relay/time", summary="Relay server time for clock-sync")
async def relay_time():
    try:
        return await relay_proxy.relay_get("time")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e


@router.get(f"/api/v1/{CAM_TYPE}/relay/pong", include_in_schema=False)
@router.get("/relay/pong", summary="Last joystick ping/pong result")
async def relay_pong():
    try:
        return await relay_proxy.relay_get("pong")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"relay unreachable: {e}") from e

@router.post(
    "/action/restart",
    response_model=ActionResponse,
    dependencies=[Depends(require_api_key)],
    summary="Restart the camera systemd service",
    description="Requires `X-API-Key`. Runs `systemctl restart` for the configured unit.",
)
def restart_service() -> ActionResponse:
    service_restart()
    return ActionResponse(ok=True, message="service restarted")

def _janus_js_response() -> FileResponse | Response:
    """Serve the Janus WebRTC JavaScript library from templates/janus.js or CDN fallback."""
    settings = get_settings()
    janus_path = Path(settings.templates_dir) / "janus.js"
    if janus_path.exists():
        return FileResponse(str(janus_path), media_type="application/javascript")
    try:
        r = requests.get(JANUS_JS_CDN_URL, timeout=15)
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=(
                "janus.js not found locally and CDN fallback failed. "
                "Add templates/janus.js or check network. Error: " + str(e)
            ),
        ) from e

def _streaming_js_response() -> FileResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / "streamer.js"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="streamer.js not found")
    return FileResponse(str(html_path), media_type="application/javascript")

def _depth_features_js_response() -> FileResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / "depth_features.js"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="depth_features.js not found")
    return FileResponse(str(html_path), media_type="application/javascript")

def _gamepad_js_response() -> FileResponse:
    """
    Serve the browser-side Gamepad driver from the same templates directory
    as `janus.js`, so it is accessible under the {CAM_TYPE} prefix.
    """
    settings = get_settings()
    js_path = Path(settings.templates_dir) / "gamepaddriver.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="gamepaddriver.js not found")
    return FileResponse(str(js_path), media_type="application/javascript")

def _gamepad_config_response() -> JSONResponse:
    """
    Serve joystick mapping configuration (axes/buttons indices).
    This allows the frontend to adapt to different physical gamepads
    without changing the JavaScript code.
    """
    import json

    settings = get_settings()
    cfg_path = Path(settings.templates_dir) / "gamepad_config.json"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="gamepad_config.json not found")

    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in gamepad_config.json: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading gamepad_config.json: {e}")

def _render_template_response(filename: str) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    raw = html_path.read_text(encoding="utf-8")
    rendered = raw.replace("__CAM_TYPE__", settings.camera_type)
    return HTMLResponse(rendered)

@router.get(f"/api/v1/{CAM_TYPE}/janus.js", include_in_schema=False)
@router.get("/janus.js", include_in_schema=False)
def janus_js() -> FileResponse:
    return _janus_js_response()

@router.get(f"/api/v1/{CAM_TYPE}/streamer.js", include_in_schema=False)
@router.get("/streamer.js", include_in_schema=False)
def streaming_js() -> FileResponse:
    return _streaming_js_response()

@router.get(f"/api/v1/{CAM_TYPE}/depth_features.js", include_in_schema=False)
@router.get("/depth_features.js", include_in_schema=False)
def depth_features_js() -> FileResponse:
    return _depth_features_js_response()


@router.get(f"/api/v1/{CAM_TYPE}/gamepaddriver.js", include_in_schema=False)
@router.get("/gamepaddriver.js", include_in_schema=False)
def gamepad_js() -> FileResponse:
    return _gamepad_js_response()

@router.get(f"/api/v1/{CAM_TYPE}/gamepad_config.json", include_in_schema=False)
@router.get("/gamepad_config.json", include_in_schema=False)
def gamepad_config() -> JSONResponse:
    return _gamepad_config_response()


@router.get(
    f"/api/v1/{CAM_TYPE}",
    response_model=HealthResponse,
    summary="Camera API root",
    description="Used by API gateway to discover upstream prefix (/api/v1/{camera_type}).",
)
def camera_api_root() -> HealthResponse:
    """Exposed in OpenAPI so the gateway can detect prefix /api/v1/{CAM_TYPE}."""
    return HealthResponse(ok=True)


def _player_script_response(path: str) -> FileResponse:
    """Serve a JS file from templates/player/; path is relative (e.g. ns.js or core/backoff.js)."""
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    base = Path(settings.templates_dir) / "player"
    file_path = (base / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail=f"Player script not found: {path}")
    return FileResponse(str(file_path), media_type="application/javascript")


@router.get(f"/api/v1/{CAM_TYPE}/player/{{path:path}}", include_in_schema=False)
def player_script(path: str) -> FileResponse:
    return _player_script_response(path)


@router.get("/player/{path:path}", include_in_schema=False)
def player_script_no_prefix(path: str) -> FileResponse:
    """Fallback when gateway uses empty upstream prefix (requests go to /player/...)."""
    return _player_script_response(path)


@router.get(f"/api/v1/{CAM_TYPE}/color_view.html", include_in_schema=False)
@router.get("/color_view.html", include_in_schema=False)
def color_view() -> HTMLResponse:
    return _render_template_response("color_view.html")

if CAM_TYPE == "depth_camera":
    class DepthResponse(BaseModel):
        type: str = "depth"
        x: float
        y: float
        depth: float

    @router.get(f"/api/v1/{CAM_TYPE}/depth_view.html", include_in_schema=False)
    @router.get("/depth_view.html", include_in_schema=False)
    def depth_view() -> HTMLResponse:
        return _render_template_response("depth_view.html")

    @router.get(f"/api/v1/{CAM_TYPE}/ir_view.html", include_in_schema=False)
    @router.get("/ir_view.html", include_in_schema=False)
    def ir_view() -> HTMLResponse:
        return _render_template_response("ir_view.html")

    depth_description = (
        "Returns the depth value at the given normalized coordinates (0..100), "
        "where (0,0) is the lower-left corner of the video frame and (100,100) is the upper-right."
    )

    @router.get(
        f"/api/v1/{CAM_TYPE}/depth",
        response_model=DepthResponse,
        summary="Get depth at specified coordinates",
        description=depth_description,
    )
    @router.get(
        "/depth",
        response_model=DepthResponse,
        summary="Get depth at specified coordinates",
        description=depth_description,
    )
    async def get_depth(
        x: Optional[float] = None,
        y: Optional[float] = None,
        message: Optional[str] = None,
    ) -> DepthResponse:
        import requests

        def parse_from_message(payload: str) -> tuple[Optional[float], Optional[float]]:
            try:
                data = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                return None, None
            x_val = data.get("x")
            y_val = data.get("y")
            try:
                x_parsed = float(x_val) if x_val is not None else None
                y_parsed = float(y_val) if y_val is not None else None
            except (TypeError, ValueError):
                return None, None
            return x_parsed, y_parsed

        if (x is None or y is None) and message:
            msg_x, msg_y = parse_from_message(message)
            x = x if x is not None else msg_x
            y = y if y is not None else msg_y

        if x is None or y is None:
            raise HTTPException(status_code=422, detail="Parameters 'x' and 'y' are required")

        def clamp_percent(value: float) -> float:
            return max(0.0, min(100.0, value))

        x = clamp_percent(x)
        y = clamp_percent(y)

        try:
            url = f"http://localhost:8000/depth?x={x}&y={y}"
            response = requests.get(url)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return DepthResponse(**response.json())
        except requests.RequestException as e:
            raise HTTPException(status_code=500, detail=f"Error getting depth: {e}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unknown error: {e}")

    color_frame_description = (
        "Returns the latest D435 colour (RGB24) frame from the RealSense color sensor. "
        "Use format=json for base64-encoded JSON payload, or format=raw for raw bytes."
    )

    @router.get(
        f"/api/v1/{CAM_TYPE}/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    @router.get(
        "/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    async def get_depth_color_frame(format: str = "json"):
        import requests as _req
        try:
            url = f"http://localhost:8000/color_frame?format={format}"
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
                        "X-Dtype": resp.headers.get("X-Dtype", "uint8-rgb24"),
                    },
                )
            return JSONResponse(content=resp.json())
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Depth color frame proxy error: {e}")

@router.get(f"/api/v1/{CAM_TYPE}/favicon.ico", include_in_schema=False)
@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    settings = get_settings()
    path = Path(settings.static_dir) / "favicon.ico"
    try:
        if path.is_file():
            return FileResponse(
                str(path),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception as exc:
        logging.warning("favicon not accessible: %s", exc)
    return Response(status_code=204)