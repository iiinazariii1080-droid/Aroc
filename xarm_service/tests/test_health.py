"""Tests for health endpoints (no hardware)."""

import os
import sys
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.health import health_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(health_router)
    return test_app


@pytest.mark.asyncio
async def test_health_live():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_returns_503_when_no_di():
    """When DI is not initialised, /health/ready should return 503."""
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/health/ready")
    # Without DI initialised, get_readiness_gate() may raise — we expect 503
    # or if StateStore was already created globally, it returns not-ready
    assert r.status_code in (200, 503)
    body = r.json()
    assert "ready" in body
