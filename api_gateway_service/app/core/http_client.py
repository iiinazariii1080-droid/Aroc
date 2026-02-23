from contextlib import asynccontextmanager
import asyncio
import logging
from typing import Dict

import httpx
from fastapi import FastAPI

from .auth_client import AuthClient
from .openapi_agg import openapi_refresh_loop, populate_openapi_cache
from .config import (
    validate_runtime_config,
    CAM_CONNECT_TIMEOUT_S,
    CAM_MAX_CONNECTIONS,
    CAM_MAX_KEEPALIVE,
    CAM_POOL_TIMEOUT_S,
    CAM_READ_TIMEOUT_S,
    CAM_WRITE_TIMEOUT_S,
    CONNECT_TIMEOUT_S,
    DEFAULT_SERVICE_CONCURRENCY,
    KEEPALIVE_EXPIRY_S,
    MAX_CONNECTIONS,
    MAX_KEEPALIVE_CONNECTIONS,
    POOL_TIMEOUT_S,
    READ_TIMEOUT_S,
    SERVICE_CONCURRENCY,
    SERVICE_MAP,
    VERIFY_TLS,
    WRITE_TIMEOUT_S,
    WS_MAX_CONCURRENT,
)

logger = logging.getLogger(__name__)


def _build_semaphores() -> Dict[str, asyncio.Semaphore]:
    """Create a concurrency semaphore for every known service."""
    sems: Dict[str, asyncio.Semaphore] = {}
    for name in SERVICE_MAP:
        limit = SERVICE_CONCURRENCY.get(name, DEFAULT_SERVICE_CONCURRENCY)
        sems[name] = asyncio.Semaphore(limit)
    return sems


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_config()

    # ── Main HTTP client (robot / igus / symovo / xarm) ─────────
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        read=READ_TIMEOUT_S,
        write=WRITE_TIMEOUT_S,
        pool=POOL_TIMEOUT_S,
    )
    limits = httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY_S,
    )
    client = httpx.AsyncClient(verify=VERIFY_TLS, timeout=timeout, limits=limits)
    app.state.http_client = client

    # ── Camera HTTP client (isolated pool, aggressive timeouts) ─
    cam_timeout = httpx.Timeout(
        connect=CAM_CONNECT_TIMEOUT_S,
        read=CAM_READ_TIMEOUT_S,
        write=CAM_WRITE_TIMEOUT_S,
        pool=CAM_POOL_TIMEOUT_S,
    )
    cam_limits = httpx.Limits(
        max_connections=CAM_MAX_CONNECTIONS,
        max_keepalive_connections=CAM_MAX_KEEPALIVE,
        keepalive_expiry=15.0,
    )
    cam_client = httpx.AsyncClient(verify=VERIFY_TLS, timeout=cam_timeout, limits=cam_limits)
    app.state.cam_http_client = cam_client

    # ── Per-service concurrency semaphores ──────────────────────
    app.state.service_semaphores = _build_semaphores()

    # ── WebSocket concurrency semaphore ─────────────────────────
    app.state.ws_semaphore = asyncio.Semaphore(WS_MAX_CONCURRENT)

    logger.info(
        "HTTP clients ready: main(max_conn=%d) camera(max_conn=%d) ws_slots=%d",
        MAX_CONNECTIONS, CAM_MAX_CONNECTIONS, WS_MAX_CONCURRENT,
    )

    # ── Auth ────────────────────────────────────────────────────
    auth_client = AuthClient()
    await auth_client.startup(app)
    app.state.auth_client = auth_client

    # ── OpenAPI cache ─────────────────────────────────────────
    await populate_openapi_cache(app)
    openapi_task = asyncio.create_task(openapi_refresh_loop(app))

    try:
        yield
    finally:
        openapi_task.cancel()
        try:
            await openapi_task
        except asyncio.CancelledError:
            pass
        await auth_client.shutdown()
        await cam_client.aclose()
        await client.aclose()


def get_http_client(app: FastAPI) -> httpx.AsyncClient:
    """Return the main HTTP client (for non-camera services)."""
    return app.state.http_client


def get_cam_http_client(app: FastAPI) -> httpx.AsyncClient:
    """Return the isolated camera HTTP client."""
    return app.state.cam_http_client


def get_service_semaphore(app: FastAPI, service: str) -> asyncio.Semaphore:
    """Return the concurrency semaphore for *service*."""
    sems: Dict[str, asyncio.Semaphore] = app.state.service_semaphores
    if service not in sems:
        # Dynamically added services get the default limit
        sems[service] = asyncio.Semaphore(DEFAULT_SERVICE_CONCURRENCY)
    return sems[service]


def get_ws_semaphore(app: FastAPI) -> asyncio.Semaphore:
    """Return the WebSocket concurrency semaphore."""
    return app.state.ws_semaphore

