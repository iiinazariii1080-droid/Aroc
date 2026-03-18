"""hub-auth microservice — JWT token management via FastAPI."""

import functools
import hmac
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Header, Response

from auth_manager import HubAuthManager
from config import get_settings
from shared.config_types import HubAuthSettings
from shared.logging_config import configure_logging
from shared.utils import now_iso

configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _get_auth_manager() -> HubAuthManager:
    settings = get_settings()
    return HubAuthManager(
        HubAuthSettings(
            base_url=settings.hub_base_url,
            robot_id=settings.hub_robot_id,
            api_key=settings.hub_api_key,
            refresh_margin=settings.hub_auth_refresh_margin,
            timeout=settings.hub_auth_timeout,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("hub-auth starting on %s:%s", settings.api_host, settings.api_port)
    _get_auth_manager()  # warm up
    yield
    _get_auth_manager().close()
    logger.info("hub-auth shutting down")


app = FastAPI(
    title="AROC Hub Auth",
    description="JWT token management for robot authentication",
    version="1.0.0",
    lifespan=lifespan,
)


def _require_internal_key(x_internal_key: str | None = Header(None)) -> None:
    """Validate INTERNAL_SERVICE_KEY on protected endpoints."""
    key = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if not key:
        return  # No key configured = auth disabled (dev mode)
    if not x_internal_key or not hmac.compare_digest(x_internal_key, key):
        raise HTTPException(status_code=401, detail="Invalid internal service key")


@app.get("/auth/headers", dependencies=[Depends(_require_internal_key)])
def get_auth_headers() -> dict:
    """Returns {"Authorization": "Bearer <jwt>"} if auth is enabled and a token is available.

    Sync handler — FastAPI runs in threadpool to avoid blocking the event loop
    during the blocking requests.Session.post() call in auth_manager.
    """
    mgr = _get_auth_manager()
    if not mgr.is_enabled():
        return {}
    return mgr.auth_headers()


@app.get("/auth/status", dependencies=[Depends(_require_internal_key)])
def get_auth_status() -> dict:
    """Token status (valid, expiry, robot_id)."""
    mgr = _get_auth_manager()
    return {
        "enabled": mgr.is_enabled(),
        **mgr.token_status(),
    }


@app.get("/health")
async def health_check(response: Response) -> dict:
    mgr = _get_auth_manager()
    enabled = mgr.is_enabled()
    has_token = mgr.has_valid_token()

    if enabled and not has_token:
        response.status_code = 503
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "enabled": enabled,
        "has_valid_token": has_token,
        "timestamp": now_iso(),
    }


@app.get("/")
async def root() -> dict:
    return {
        "service": "hub-auth",
        "version": "1.0.0",
        "endpoints": {
            "GET /auth/headers": "Get auth headers with JWT",
            "GET /auth/status": "Token status",
            "GET /health": "Health check",
        },
    }


if __name__ == "__main__":
    settings = get_settings()
    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
