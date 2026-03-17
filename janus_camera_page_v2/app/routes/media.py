"""Static and template asset serving routes.

Serves JavaScript files, HTML views, gamepad config, and favicon from the
templates/static directories. Extracted from system.py to reduce god-controller bloat.

Versioned paths (/api/v1/{cam_type}/...) are registered at runtime in
register_routes() via app.add_api_route() so that CAM_TYPE overrides via
environment variables (e.g. in tests) take effect correctly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.core.settings import get_settings
from app.utils.templates import get_jinja

router = APIRouter(tags=["media"])

log = logging.getLogger(__name__)


# ── Private helpers ──────────────────────────────────────────────────────────

def _serve_template_js(filename: str, *, missing_status: int = 404) -> FileResponse:
    """Serve a JavaScript file from the templates directory.

    ``missing_status=503`` is used for janus.js (operator-deployed file that
    must be present before the service starts); all other files use 404.
    """
    settings = get_settings()
    path = Path(settings.templates_dir) / filename
    if not path.exists():
        detail = (
            f"{filename} not found at {path}. "
            f"Deploy templates/{filename} before starting the service."
            if missing_status == 503
            else f"{filename} not found"
        )
        raise HTTPException(status_code=missing_status, detail=detail)
    return FileResponse(str(path), media_type="application/javascript")


def _gamepad_config_response() -> JSONResponse:
    """
    Serve joystick mapping configuration (axes/buttons indices).
    This allows the frontend to adapt to different physical gamepads
    without changing the JavaScript code.
    """
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
    try:
        template = get_jinja().get_template(filename)
    except Exception:
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    ctx = {
        "cam_type": settings.camera_type,
        # Depth camera node has no relay/joystick — suppress to avoid 502 spam
        "joystick_mode": "off" if settings.camera_type == "depth_camera" else "always",
    }
    return HTMLResponse(template.render(**ctx))


def _player_script_response(path: str) -> FileResponse:
    """Serve a JS file from templates/player/; path is relative (e.g. ns.js or core/backoff.js).

    Only ``.js`` files are served — documentation (.md) and other non-script
    files are blocked to prevent information leakage.
    """
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")
    if not path.endswith(".js"):
        raise HTTPException(status_code=404, detail="Only .js files are served")
    settings = get_settings()
    base = Path(settings.templates_dir) / "player"
    file_path = (base / path).resolve()
    if not file_path.is_file() or not file_path.is_relative_to(base):
        raise HTTPException(status_code=404, detail=f"Player script not found: {path}")
    return FileResponse(str(file_path), media_type="application/javascript")


# ── JavaScript assets ────────────────────────────────────────────────────────

@router.get("/janus.js", include_in_schema=False)
def janus_js() -> FileResponse:
    return _serve_template_js("janus.js", missing_status=503)

@router.get("/streamer.js", include_in_schema=False)
def streaming_js() -> FileResponse:
    return _serve_template_js("streamer.js")

@router.get("/depth_features.js", include_in_schema=False)
def depth_features_js() -> FileResponse:
    return _serve_template_js("depth_features.js")

@router.get("/gripper_reticle.js", include_in_schema=False)
def gripper_reticle_js() -> FileResponse:
    return _serve_template_js("gripper_reticle.js")

@router.get("/gamepaddriver.js", include_in_schema=False)
def gamepad_js() -> FileResponse:
    return _serve_template_js("gamepaddriver.js")

@router.get("/gamepad_config.json", include_in_schema=False)
def gamepad_config() -> JSONResponse:
    return _gamepad_config_response()


# ── Player bundle ────────────────────────────────────────────────────────────

@router.get("/player/{path:path}", include_in_schema=False)
def player_script_no_prefix(path: str) -> FileResponse:
    """Fallback when gateway uses empty upstream prefix (requests go to /player/...)."""
    return _player_script_response(path)


# ── HTML views ───────────────────────────────────────────────────────────────

@router.get("/color_view.html", include_in_schema=False)
def color_view() -> HTMLResponse:
    return _render_template_response("color_view.html")


# ── Favicon ──────────────────────────────────────────────────────────────────

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
        log.warning("favicon not accessible: %s", exc)
    return Response(status_code=204)
