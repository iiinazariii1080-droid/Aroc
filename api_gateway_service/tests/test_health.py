"""Integration tests for health endpoints: /livez, /healthz, /readyz."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import pytest


@pytest.mark.asyncio
async def test_livez(client):
    resp = await client.get("/livez")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["http_client_ready"] is True
    assert isinstance(data["services_configured"], int)
    assert "openapi_refresh" in data
    assert isinstance(data["openapi_refresh"]["failure_reasons"], dict)
    assert "service_error_rates" in data
    assert "proxy_services" in data["service_error_rates"]
    assert "auth" in data["service_error_rates"]


@pytest.mark.asyncio
async def test_readyz_ok(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"


@pytest.mark.asyncio
async def test_readyz_degraded_when_auth_missing_token(client, app):
    app.state.auth_client.describe = AsyncMock(
        return_value={
            "token_present": False,
            "consecutive_failures": 3,
            "last_error": "auth endpoint returned 404",
        }
    )

    resp = await client.get("/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert any(item.get("service") == "auth" for item in data["failed"])


@pytest.mark.asyncio
async def test_healthz_reports_proxy_service_error_rate(client):
    with patch("app.routers.proxy_http.stream_request", side_effect=httpx.ConnectError("offline")):
        resp = await client.get("/api/v1/xarm/status")
        assert resp.status_code == 502

    health = await client.get("/healthz")
    assert health.status_code == 200
    metrics = health.json()["service_error_rates"]["proxy_services"]
    assert "xarm" in metrics
    assert metrics["xarm"]["errors"] >= 1
    assert metrics["xarm"]["error_rate"] > 0
