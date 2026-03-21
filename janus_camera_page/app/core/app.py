import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.events import register_event_handlers
from app.core.settings import get_settings
from app.routes import register_routes
from shared_config.network import DEVICES, PORTS

# frame-ancestors requires exact origin-s, not CIDR notation.
# Default: the two LAN nodes that may embed the player.
# Override via CSP_FRAME_ANCESTORS_LAN env var for different deployments.
_FRAME_ANCESTORS_LAN = os.environ.get(
    "CSP_FRAME_ANCESTORS_LAN",
    f"http://{DEVICES.HOST_LAN_IP}:{PORTS.COLOR_CAMERA} "
    f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA} "
    f"https://blupassionsystem.de:8443 "
    f"http://212.227.150.180:3456",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response (P2.9)."""

    async def dispatch(self, request: Request, call_next):
        # Correlation ID: propagate incoming or generate new
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = req_id
        style_nonce = uuid.uuid4().hex[:16]
        request.state.style_nonce = style_nonce

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options removed: CSP frame-ancestors is the modern
        # replacement and already allows cross-origin embedding from
        # *.techvisioncloud.pl.  Having both creates a contradiction
        # (SAMEORIGIN vs cross-origin frame-ancestors).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            f"style-src 'self' 'nonce-{style_nonce}'; "
            f"connect-src 'self' "
            f"ws://{DEVICES.HOST_LAN_IP}:* ws://{DEVICES.DEPTH_CAMERA_IP}:* "
            f"ws://127.0.0.1:* ws://localhost:* "
            f"wss://{DEVICES.HOST_LAN_IP}:* wss://{DEVICES.DEPTH_CAMERA_IP}:* "
            f"wss://*.techvisioncloud.pl; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            f"frame-ancestors 'self' https://*.techvisioncloud.pl {_FRAME_ANCESTORS_LAN}"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        return response


def create_app() -> FastAPI:
    from app.core.admin import validate_admin_config
    validate_admin_config()

    settings = get_settings()
    application = FastAPI(title=settings.app_title, version=settings.app_version)

    application.add_middleware(SecurityHeadersMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
    )

    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    register_routes(application)
    register_event_handlers(application)
    return application

