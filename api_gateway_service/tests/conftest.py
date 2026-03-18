"""
Shared test fixtures.

Provides a fully wired FastAPI test app with a mock httpx.AsyncClient
so tests run without any real upstream services.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Ensure structured logging is initialised before the app is imported
from app.core.logging_cfg import setup_logging

setup_logging()


@pytest.fixture()
def mock_http_client():
    """A mock httpx.AsyncClient that can be configured per test."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.is_closed = False
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    client.get = AsyncMock(return_value=mock_resp)
    return client


@pytest.fixture(autouse=True)
def _reset_hub_state():
    """Reset the global hub state store for test isolation."""
    from app.core.hub_state import hub_state_store
    hub_state_store._state = {
        "hub_config": None,
        "robot_identity": None,
        "robot_credentials_meta": None,
    }
    hub_state_store._loaded = True
    # Neuter disk I/O during tests
    hub_state_store._flush = AsyncMock()
    yield


@pytest.fixture(autouse=True)
def _reset_secret_store():
    """Reset the in-memory secret store."""
    from app.core.secret_store import robot_secret_store
    import asyncio
    robot_secret_store._api_key = None
    yield


@pytest.fixture(autouse=True)
def _reset_service_metrics():
    from app.core.service_metrics import reset_service_error_metrics
    reset_service_error_metrics()
    yield
    reset_service_error_metrics()


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Clear the global circuit breaker registry between tests to prevent
    accumulation of CircuitBreaker instances (each holding an asyncio.Lock)."""
    from app.core.circuit_breaker import _breakers
    _breakers.clear()
    yield
    _breakers.clear()


@pytest.fixture()
def app(mock_http_client):
    """
    Build a fresh FastAPI app with all routers but NO real lifespan
    (no real HTTP client, no auth loop).
    """
    from app.core.config import ALLOWED_ORIGINS
    from app.core.openapi_agg import setup_custom_openapi
    from app.routers import health, hub, metrics_prom, proxy_http, proxy_ws, favicon

    @asynccontextmanager
    async def _noop_lifespan(a: FastAPI):
        a.state.http_client = mock_http_client
        # Provide a no-op auth_client
        auth = MagicMock()
        auth.startup = AsyncMock()
        auth.shutdown = AsyncMock()
        auth.describe = AsyncMock(return_value={"token_present": True, "consecutive_failures": 0, "last_error": None})
        auth.force_refresh = AsyncMock()
        a.state.auth_client = auth
        # Per-service concurrency semaphores
        a.state.service_semaphores = {}
        a.state.ws_semaphore = asyncio.Semaphore(30)
        yield

    test_app = FastAPI(title="API Gateway", lifespan=_noop_lifespan)

    # Reproduce middleware stack from main.py
    from main import RequestIDMiddleware
    from fastapi.middleware.cors import CORSMiddleware

    test_app.add_middleware(RequestIDMiddleware)
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Key", "X-Request-ID"],
    )

    test_app.include_router(health.router)
    test_app.include_router(hub.router)
    test_app.include_router(metrics_prom.router)
    test_app.include_router(proxy_ws.router)
    test_app.include_router(proxy_http.router)  # catch-all — must be last
    test_app.include_router(favicon.router)

    setup_custom_openapi(test_app)
    return test_app


@pytest.fixture()
async def client(app):
    """Async httpx test client wired to the test app via lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Manually trigger lifespan startup
        scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
        startup_complete = asyncio.Event()
        shutdown_trigger = asyncio.Event()

        async def receive():
            if not startup_complete.is_set():
                startup_complete.set()
                return {"type": "lifespan.startup"}
            await shutdown_trigger.wait()
            return {"type": "lifespan.shutdown"}

        async def send(msg):
            pass

        lifespan_task = asyncio.create_task(app(scope, receive, send))
        # Wait for startup to be processed
        await startup_complete.wait()
        await asyncio.sleep(0.05)

        yield ac

        shutdown_trigger.set()
        await lifespan_task
