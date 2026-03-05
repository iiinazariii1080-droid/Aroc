from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.events import register_event_handlers
from app.core.settings import get_settings
from app.routes import register_routes


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response (P2.9)."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' wss: ws: https://*.techvisioncloud.pl; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "frame-ancestors 'self' https://*.techvisioncloud.pl http://192.168.1.0/24"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_title, version=settings.app_version)

    application.add_middleware(SecurityHeadersMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    register_routes(application)
    register_event_handlers(application)
    return application

