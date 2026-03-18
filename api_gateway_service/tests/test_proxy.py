"""Integration tests for the HTTP proxy router."""

from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.core.config import settings
from tests.helpers import make_mock_response as _make_mock_response


@pytest.mark.asyncio
async def test_proxy_known_service(client, mock_http_client):
    """Proxy forwards request to a configured upstream service."""
    mock_resp = _make_mock_response(200, b'{"data":"hello"}')

    with patch("app.routers.proxy_http.stream_request", return_value=mock_resp) as sr:
        resp = await client.get("/api/v1/xarm/status")

    assert resp.status_code == 200
    assert resp.json() == {"data": "hello"}
    sr.assert_called_once()
    # Verify the upstream URL contains the service base
    call_args = sr.call_args
    assert "8102" in call_args[0][2]


@pytest.mark.asyncio
async def test_proxy_unknown_service(client):
    """Proxy returns 404 for unknown service names."""
    resp = await client.get("/api/v1/nonexistent/some/path")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_proxy_streams_response_headers(client, mock_http_client):
    """Upstream response headers are forwarded (minus hop-by-hop)."""
    mock_resp = _make_mock_response(
        201,
        b"created",
        headers={"content-type": "text/plain", "x-upstream-id": "abc123"},
    )

    with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
        resp = await client.post("/api/v1/robot/action", content=b"test")

    assert resp.status_code == 201
    assert resp.headers.get("x-upstream-id") == "abc123"


@pytest.mark.asyncio
async def test_proxy_preserves_query_params(client, mock_http_client):
    """Query parameters are forwarded to upstream."""
    mock_resp = _make_mock_response()

    with patch("app.routers.proxy_http.stream_request", return_value=mock_resp) as sr:
        await client.get("/api/v1/igus/devices?page=2&limit=10")

    call_kwargs = sr.call_args[1]
    params = dict(call_kwargs["params"])
    assert params["page"] == "2"
    assert params["limit"] == "10"


@pytest.mark.asyncio
async def test_proxy_request_id_header(client):
    """Proxy responses include X-Request-ID header."""
    mock_resp = _make_mock_response()

    with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
        resp = await client.get("/api/v1/xarm/status")

    assert "x-request-id" in resp.headers


@pytest.mark.asyncio
async def test_proxy_body_too_large(client, mock_http_client):
    """Returns 413 when request body exceeds MAX_REQUEST_BODY_BYTES."""
    with patch.object(settings, "max_request_body_bytes", 10):
        mock_resp = _make_mock_response()
        with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
            resp = await client.post("/api/v1/robot/data", content=b"x" * 100)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_proxy_root_route(client, mock_http_client):
    """Proxy handles root service path (no trailing subpath)."""
    mock_resp = _make_mock_response()

    with patch("app.routers.proxy_http.stream_request", return_value=mock_resp):
        resp = await client.get("/api/v1/igus")

    assert resp.status_code == 200
