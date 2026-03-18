from contextlib import asynccontextmanager
import asyncio
import logging
import os
from typing import Dict

import httpx
from fastapi import FastAPI

from .auth_client import AuthClient
from .hub_state import hub_state_store
from .openapi_agg import openapi_refresh_loop, populate_openapi_cache
from .config import (
    validate_runtime_config,
    settings,
    SERVICE_CONCURRENCY,
    SERVICE_MAP,
)

logger = logging.getLogger(__name__)


def _build_semaphores() -> Dict[str, asyncio.Semaphore]:
    """Create a concurrency semaphore for every known service."""
    sems: Dict[str, asyncio.Semaphore] = {}
    for name in SERVICE_MAP:
        limit = SERVICE_CONCURRENCY.get(name, settings.default_service_concurrency)
        sems[name] = asyncio.Semaphore(limit)
    return sems


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_config()

    # Guard: stateful singletons (circuit breakers, hub_state, secret_store,
    # metrics) live in-process memory and cannot be shared across workers.
    _worker_count = int(os.getenv("WEB_CONCURRENCY", "1"))
    if _worker_count > 1:
        raise RuntimeError(
            f"API Gateway requires exactly 1 worker (got WEB_CONCURRENCY={_worker_count}). "
            "Stateful components are not safe for multi-worker deployment."
        )

    if not settings.gateway_admin_key:
        logger.warning(
            "GATEWAY_ADMIN_KEY is not set — hub management endpoints are unprotected. "
            "Set GATEWAY_ADMIN_KEY env var for production deployments.",
        )

    # ── Hub state (load from disk once at startup) ─────────────
    await hub_state_store.load()

    # ── Main HTTP client (robot / igus / symovo / xarm) ─────────
    s = settings
    timeout = httpx.Timeout(
        connect=s.http_connect_timeout,
        read=s.http_read_timeout,
        write=s.http_write_timeout,
        pool=s.http_pool_timeout,
    )
    limits = httpx.Limits(
        max_connections=s.http_max_connections,
        max_keepalive_connections=s.http_max_keepalive_connections,
        keepalive_expiry=s.http_keepalive_expiry,
    )
    client = httpx.AsyncClient(verify=s.verify_tls, timeout=timeout, limits=limits)
    app.state.http_client = client

    # ── Camera HTTP client (isolated pool, aggressive timeouts) ─
    cam_timeout = httpx.Timeout(
        connect=s.cam_connect_timeout,
        read=s.cam_read_timeout,
        write=s.cam_write_timeout,
        pool=s.cam_pool_timeout,
    )
    cam_limits = httpx.Limits(
        max_connections=s.cam_max_connections,
        max_keepalive_connections=s.cam_max_keepalive,
        keepalive_expiry=s.cam_keepalive_expiry,
    )
    cam_client = httpx.AsyncClient(verify=s.verify_tls, timeout=cam_timeout, limits=cam_limits)
    app.state.cam_http_client = cam_client

    # ── Per-service concurrency semaphores ──────────────────────
    app.state.service_semaphores = _build_semaphores()

    # ── WebSocket concurrency semaphore ─────────────────────────
    app.state.ws_semaphore = asyncio.Semaphore(s.ws_max_concurrent)

    logger.info(
        "HTTP clients ready: main(max_conn=%d) camera(max_conn=%d) ws_slots=%d",
        s.http_max_connections, s.cam_max_connections, s.ws_max_concurrent,
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
        sems[service] = asyncio.Semaphore(settings.default_service_concurrency)
    return sems[service]


def get_ws_semaphore(app: FastAPI) -> asyncio.Semaphore:
    """Return the WebSocket concurrency semaphore."""
    return app.state.ws_semaphore

