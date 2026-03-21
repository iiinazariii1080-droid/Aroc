"""Static asset and HTML template serving routes.

Serves Janus JS library, streamer, gamepad driver, player framework scripts,
and rendered HTML views (color_view, depth_view, ir_view).

Uses Jinja2 directly (not Starlette TemplateResponse) for version-proof
rendering with autoescape enabled.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.core.settings import get_settings

router = APIRouter(tags=["templates"])

# Boot-time constant — FastAPI route paths must be static at decoration time.
_CAM_TYPE = get_settings().camera_type

# Jinja2 env with autoescape — used directly, bypasses Starlette wrapper
# to avoid TemplateResponse API differences across Starlette versions.
_jinja_env = Environment(
    loader=FileSystemLoader(str(get_settings().templates_dir)),
    autoescape=select_autoescape(["html", "htm"]),
)

# Fallback CDN for janus.js when templates/janus.js is not present
JANUS_JS_CDN_URL = "https://cdn.jsdelivr.net/gh/meetecho/janus-gateway@v1.2.4/html/janus.js"


def _serve_template_file(filename: str, media_type: str = "application/javascript") -> FileResponse:
    """Serve a single file from the templates directory."""
    settings = get_settings()
    path = Path(settings.templates_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return FileResponse(str(path), media_type=media_type)


def _render_jinja(template_name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _render_template_response(filename: str, request: Request) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    joystick_mode = "off" if settings.camera_type == "depth_camera" else "always"
    style_nonce = getattr(request.state, "style_nonce", "")
    return _render_jinja(
        filename,
        cam_type=settings.camera_type,
        joystick_mode=joystick_mode,
        stream_id=settings.janus_color_stream_id,
        stream_name="RealSense RGB",
        depth_features_script=False,
        style_nonce=style_nonce,
    )


def _render_color_view_variant(
    stream_id: int,
    stream_name: str,
    request: Request,
    joystick: bool = True,
    depth_features: bool = False,
) -> HTMLResponse:
    settings = get_settings()
    html_path = Path(settings.templates_dir) / "color_view.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="color_view.html not found")
    style_nonce = getattr(request.state, "style_nonce", "")
    return _render_jinja(
        "color_view.html",
        cam_type=settings.camera_type,
        stream_id=stream_id,
        stream_name=stream_name,
        joystick_mode="always" if joystick else "off",
        depth_features_script=depth_features,
        style_nonce=style_nonce,
    )


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


# ── Static assets via API prefix (for reverse proxy compatibility) ──

@router.get(f"/api/v1/{_CAM_TYPE}/static/{{path:path}}", include_in_schema=False)
def static_via_api(path: str) -> FileResponse:
    """Serve static files via API prefix so they route through the reverse proxy."""
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    file_path = (Path(settings.static_dir) / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(Path(settings.static_dir).resolve()):
        raise HTTPException(status_code=404, detail=f"Static file not found: {path}")
    suffix = file_path.suffix.lower()
    media_types = {".css": "text/css", ".js": "application/javascript", ".png": "image/png",
                   ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
    return FileResponse(str(file_path), media_type=media_types.get(suffix, "application/octet-stream"))


# ── Player framework scripts ──

def _player_script_response(path: str) -> FileResponse:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    settings = get_settings()
    base = Path(settings.templates_dir) / "player"
    file_path = (base / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail=f"Player script not found: {path}")
    return FileResponse(
        str(file_path),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get(f"/api/v1/{_CAM_TYPE}/player/{{path:path}}", include_in_schema=False)
def player_script(path: str) -> FileResponse:
    return _player_script_response(path)


@router.get("/player/{path:path}", include_in_schema=False)
def player_script_no_prefix(path: str) -> FileResponse:
    return _player_script_response(path)


# ── HTML views ──

@router.get(f"/api/v1/{_CAM_TYPE}/color_view.html", include_in_schema=False)
@router.get("/color_view.html", include_in_schema=False)
def color_view(request: Request) -> HTMLResponse:
    return _render_template_response("color_view.html", request)


if _CAM_TYPE == "depth_camera":
    @router.get(f"/api/v1/{_CAM_TYPE}/depth_view.html", include_in_schema=False)
    @router.get("/depth_view.html", include_in_schema=False)
    def depth_view(request: Request) -> HTMLResponse:
        settings = get_settings()
        style_nonce = getattr(request.state, "style_nonce", "")
        depth_template = Path(settings.templates_dir) / "depth_view.html"
        if depth_template.exists():
            return _render_jinja("depth_view.html", cam_type=settings.camera_type, style_nonce=style_nonce, stream_id=settings.janus_depth_stream_id)
        return _render_color_view_variant(settings.janus_depth_stream_id, "RealSense Depth", request, joystick=False, depth_features=True)

    @router.get(f"/api/v1/{_CAM_TYPE}/ir_view.html", include_in_schema=False)
    @router.get("/ir_view.html", include_in_schema=False)
    def ir_view(request: Request) -> HTMLResponse:
        settings = get_settings()
        style_nonce = getattr(request.state, "style_nonce", "")
        ir_template = Path(settings.templates_dir) / "ir_view.html"
        if ir_template.exists():
            return _render_jinja("ir_view.html", cam_type=settings.camera_type, style_nonce=style_nonce, stream_id=settings.janus_ir_stream_id)
        return _render_color_view_variant(settings.janus_ir_stream_id, "RealSense IR", request, joystick=False)
