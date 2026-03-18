"""Static asset and HTML template serving routes.

Serves Janus JS library, streamer, gamepad driver, player framework scripts,
and rendered HTML views (color_view, depth_view, ir_view).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.core.settings import get_settings

router = APIRouter(tags=["templates"])

# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type

# Fallback CDN for janus.js when templates/janus.js is not present
JANUS_JS_CDN_URL = "https://cdn.jsdelivr.net/gh/meetecho/janus-gateway@v1.2.4/html/janus.js"


def _serve_template_file(filename: str, media_type: str = "application/javascript") -> FileResponse:
    """Serve a single file from the templates directory."""
    settings = get_settings()
    path = Path(settings.templates_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return FileResponse(str(path), media_type=media_type)


def _render_template_response(filename: str) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    raw = html_path.read_text(encoding="utf-8")
    rendered = raw.replace("__CAM_TYPE__", settings.camera_type)
    if settings.camera_type == "depth_camera":
        rendered = rendered.replace('data-joystick-mode="always"', 'data-joystick-mode="off"')
    return HTMLResponse(rendered)


def _render_color_view_variant(
    stream_id: int,
    stream_name: str,
    joystick: bool = True,
    depth_features: bool = False,
) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / "color_view.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="color_view.html not found")
    raw = html_path.read_text(encoding="utf-8")
    rendered = raw.replace("__CAM_TYPE__", settings.camera_type)
    rendered = rendered.replace('data-prefer-stream-id="1305"', f'data-prefer-stream-id="{stream_id}"')
    rendered = rendered.replace('data-stream-name="RealSense RGB"', f'data-stream-name="{stream_name}"')
    if not joystick:
        rendered = rendered.replace('data-joystick-mode="always"', 'data-joystick-mode="off"')
    if depth_features:
        depth_script = f'<script src="/api/v1/{settings.camera_type}/depth_features.js"></script>'
        rendered = rendered.replace('</body>', f'{depth_script}\n</body>')
    return HTMLResponse(rendered)


# ── Janus JS library ──

@router.get(f"/api/v1/{_CAM_TYPE}/janus.js", include_in_schema=False, response_model=None)
@router.get("/janus.js", include_in_schema=False, response_model=None)
def janus_js():
    settings = get_settings()
    janus_path = Path(settings.templates_dir) / "janus.js"
    if janus_path.exists():
        return FileResponse(str(janus_path), media_type="application/javascript")
    try:
        r = httpx.get(JANUS_JS_CDN_URL, timeout=15)
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"janus.js not found locally and CDN fallback failed: {e}",
        ) from e


# ── JS assets ──

@router.get(f"/api/v1/{_CAM_TYPE}/streamer.js", include_in_schema=False)
@router.get("/streamer.js", include_in_schema=False)
def streaming_js() -> FileResponse:
    return _serve_template_file("streamer.js")


@router.get(f"/api/v1/{_CAM_TYPE}/depth_features.js", include_in_schema=False)
@router.get("/depth_features.js", include_in_schema=False)
def depth_features_js() -> FileResponse:
    return _serve_template_file("depth_features.js")


@router.get(f"/api/v1/{_CAM_TYPE}/gripper_reticle.js", include_in_schema=False)
@router.get("/gripper_reticle.js", include_in_schema=False)
def gripper_reticle_js() -> FileResponse:
    return _serve_template_file("gripper_reticle.js")


@router.get(f"/api/v1/{_CAM_TYPE}/gamepaddriver.js", include_in_schema=False)
@router.get("/gamepaddriver.js", include_in_schema=False)
def gamepad_js() -> FileResponse:
    return _serve_template_file("gamepaddriver.js")


@router.get(f"/api/v1/{_CAM_TYPE}/gamepad_config.json", include_in_schema=False)
@router.get("/gamepad_config.json", include_in_schema=False)
def gamepad_config() -> JSONResponse:
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


# ── Player framework scripts ──

def _player_script_response(path: str) -> FileResponse:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    base = Path(settings.templates_dir) / "player"
    file_path = (base / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail=f"Player script not found: {path}")
    return FileResponse(str(file_path), media_type="application/javascript")


@router.get(f"/api/v1/{_CAM_TYPE}/player/{{path:path}}", include_in_schema=False)
def player_script(path: str) -> FileResponse:
    return _player_script_response(path)


@router.get("/player/{path:path}", include_in_schema=False)
def player_script_no_prefix(path: str) -> FileResponse:
    return _player_script_response(path)


# ── HTML views ──

@router.get(f"/api/v1/{_CAM_TYPE}/color_view.html", include_in_schema=False)
@router.get("/color_view.html", include_in_schema=False)
def color_view() -> HTMLResponse:
    return _render_template_response("color_view.html")


if _CAM_TYPE == "depth_camera":
    @router.get(f"/api/v1/{_CAM_TYPE}/depth_view.html", include_in_schema=False)
    @router.get("/depth_view.html", include_in_schema=False)
    def depth_view() -> HTMLResponse:
        settings = get_settings()
        depth_template = Path(settings.templates_dir) / "depth_view.html"
        if depth_template.exists():
            return _render_template_response("depth_view.html")
        return _render_color_view_variant(1306, "RealSense Depth", joystick=False, depth_features=True)

    @router.get(f"/api/v1/{_CAM_TYPE}/ir_view.html", include_in_schema=False)
    @router.get("/ir_view.html", include_in_schema=False)
    def ir_view() -> HTMLResponse:
        settings = get_settings()
        ir_template = Path(settings.templates_dir) / "ir_view.html"
        if ir_template.exists():
            return _render_template_response("ir_view.html")
        return _render_color_view_variant(1307, "RealSense IR", joystick=False)
