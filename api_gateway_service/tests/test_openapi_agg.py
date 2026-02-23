"""Tests for OpenAPI aggregation — caching, parallel fetch, fallback."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.core.openapi_agg import (
    aggregate_services_openapi,
    _fetch_one_spec,
    get_openapi_refresh_metrics,
    populate_openapi_cache,
    setup_custom_openapi,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """No module-level cache to reset — cache lives on app.state now."""
    yield


def _make_app(http_client=None):
    app = FastAPI(title="Test GW")
    from app.routers import health
    app.include_router(health.router)
    if http_client is not None:
        app.state.http_client = http_client
    return app


@pytest.mark.asyncio
async def test_aggregate_without_client():
    """When http_client is missing, returns gateway-only schema."""
    app = _make_app()
    schema = await aggregate_services_openapi(app)
    assert "paths" in schema
    assert schema["info"]["title"] == "Test GW"


@pytest.mark.asyncio
async def test_aggregate_with_unreachable_upstreams():
    """When all upstreams fail, returns gateway-only schema without crashing."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    app = _make_app(http_client=client)

    schema = await aggregate_services_openapi(app)
    assert "paths" in schema
    # Should still have gateway's own paths
    assert any("/livez" in p or "/healthz" in p for p in schema["paths"])


@pytest.mark.asyncio
async def test_aggregate_merges_upstream_spec():
    """An upstream spec's paths are merged under /api/v1/{service}/."""
    upstream_spec = {
        "paths": {
            "/status": {"get": {"summary": "Robot status"}},
        },
        "components": {
            "schemas": {"StatusModel": {"type": "object"}},
        },
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = upstream_spec

    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    client.get = AsyncMock(return_value=mock_resp)
    app = _make_app(http_client=client)

    schema = await aggregate_services_openapi(app)
    # At least one service's path should appear
    service_paths = [p for p in schema["paths"] if p.startswith("/api/v1/")]
    assert len(service_paths) > 0


@pytest.mark.asyncio
async def test_fetch_one_spec_returns_none_on_error():
    """_fetch_one_spec returns (key, None) on network errors."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))

    key, spec = await _fetch_one_spec(client, "xarm", {"url": "http://localhost:9999", "prefix": ""})
    assert key == "xarm"
    assert spec is None


@pytest.mark.asyncio
async def test_fetch_one_spec_returns_data_on_success():
    """_fetch_one_spec returns the parsed JSON on success."""
    expected = {"paths": {"/foo": {}}}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = expected

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=mock_resp)

    key, spec = await _fetch_one_spec(client, "igus", {"url": "http://localhost:8101", "prefix": ""})
    assert key == "igus"
    assert spec == expected


@pytest.mark.asyncio
async def test_populate_openapi_cache_records_success_metrics():
    app = _make_app()
    setup_custom_openapi(app)

    await populate_openapi_cache(app)

    metrics = get_openapi_refresh_metrics(app)
    assert metrics["attempts"] == 1
    assert metrics["successes"] == 1
    assert metrics["failures"] == 0
    assert metrics["last_success_at"] is not None


@pytest.mark.asyncio
async def test_populate_openapi_cache_records_failure_reason_and_fallback():
    app = _make_app()
    setup_custom_openapi(app)

    with patch("app.core.openapi_agg.aggregate_services_openapi", side_effect=ValueError("bad schema")):
        await populate_openapi_cache(app)

    metrics = get_openapi_refresh_metrics(app)
    assert metrics["attempts"] == 1
    assert metrics["successes"] == 0
    assert metrics["failures"] == 1
    assert metrics["failure_reasons"]["schema_value_error"] == 1
    assert metrics["last_failure_reason"] == "schema_value_error"
    assert "bad schema" in metrics["last_failure_error"]
    assert "paths" in app.state._openapi_cache


@pytest.mark.asyncio
async def test_openapi_endpoint_handles_20_concurrent_clients(client):
    """/openapi.json should serve cached schema fast and consistently under 20 concurrent clients."""
    n = 20
    tasks = [client.get("/openapi.json") for _ in range(n)]
    results = await asyncio.gather(*tasks)

    assert len(results) == n
    assert all(r.status_code == 200 for r in results)

    payloads = [r.json() for r in results]
    assert all("paths" in p and "info" in p for p in payloads)
    baseline_title = payloads[0]["info"]["title"]
    assert all(p["info"]["title"] == baseline_title for p in payloads)
