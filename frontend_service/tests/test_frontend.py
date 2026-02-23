"""Tests for frontend_service — health, routing, config."""

import os
import sys
import pytest
import respx
from httpx import Response

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.mark.asyncio
async def test_health_check(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["service"] == "frontend"


@pytest.mark.asyncio
async def test_root_serves_html(client):
    """GET / should return index.html (HTML response)."""
    r = await client.get("/")
    assert r.status_code == 200
    # index.html should be an HTML file
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_spa_fallback_returns_html(client):
    """Unknown paths should return index.html for SPA routing."""
    r = await client.get("/some/spa/route")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_api_prefix_not_found_without_backend(client):
    """API routes should try to proxy — without backend they may fail."""
    # We use respx to mock the upstream call
    from config import API_GATEWAY_URL

    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/nonexistent").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )
        r = await client.get("/api/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_proxy_forwards_get(client):
    """GET /api/something should be proxied to API gateway."""
    from config import API_GATEWAY_URL

    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/igus/status").mock(
            return_value=Response(200, json={"position": 100, "homed": True})
        )
        r = await client.get("/api/igus/status")
    assert r.status_code == 200
    assert r.json()["position"] == 100


@pytest.mark.asyncio
async def test_api_proxy_forwards_post(client):
    """POST /api/something should be proxied to API gateway."""
    from config import API_GATEWAY_URL

    with respx.mock:
        respx.post(f"{API_GATEWAY_URL.rstrip('/')}/api/xarm/move/change_joints").mock(
            return_value=Response(200, json={"success": True})
        )
        r = await client.post("/api/xarm/move/change_joints", json={"j1": 10})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_config_values():
    """Config should have valid defaults."""
    from config import HOST, PORT, API_GATEWAY_URL, ALLOWED_ORIGINS
    assert isinstance(HOST, str)
    assert isinstance(PORT, int)
    assert PORT > 0
    assert API_GATEWAY_URL.startswith("http")
    assert isinstance(ALLOWED_ORIGINS, list)
