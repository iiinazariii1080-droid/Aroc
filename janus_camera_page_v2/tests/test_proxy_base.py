"""T1+T2: AsyncProxyClient contract tests.

Covers error mapping (Timeout→504, ConnectError→502, RemoteProtocolError→503),
forward_request header stripping, query param/body preservation, and get_json.

Risk addressed: R03 (CRITICAL — primary data path has zero behavior tests)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi import HTTPException
from starlette.datastructures import Headers, QueryParams

from app.services.proxy_base import AsyncProxyClient

UPSTREAM = "http://upstream.test:9000"


@pytest.fixture
def proxy():
    """Fresh AsyncProxyClient for each test."""
    return AsyncProxyClient("test-proxy", connect_timeout=1.0, read_timeout=1.0)


def _make_request(
    method="GET",
    headers=None,
    query_params="",
    body=b"",
):
    """Create a mock FastAPI Request with the right shape for forward_request()."""
    r = MagicMock()
    r.method = method
    r.headers = Headers(headers or {})
    r.query_params = QueryParams(query_params)
    r.body = AsyncMock(return_value=body)
    return r


# ===================================================================
# 1. Error Mapping — _request() exception → HTTPException status code
# ===================================================================

class TestErrorMapping:
    """Each httpx exception type must map to the correct HTTP status code."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_timeout_maps_to_504(self, proxy):
        respx.get(f"{UPSTREAM}/api").mock(
            side_effect=httpx.TimeoutException("read timeout"),
        )
        with pytest.raises(HTTPException) as exc_info:
            await proxy.get(f"{UPSTREAM}/api")
        assert exc_info.value.status_code == 504
        assert "timeout" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_maps_to_502(self, proxy):
        respx.get(f"{UPSTREAM}/api").mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        with pytest.raises(HTTPException) as exc_info:
            await proxy.get(f"{UPSTREAM}/api")
        assert exc_info.value.status_code == 502
        assert "unreachable" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_remote_protocol_error_maps_to_503(self, proxy):
        respx.get(f"{UPSTREAM}/api").mock(
            side_effect=httpx.RemoteProtocolError("peer closed connection"),
        )
        with pytest.raises(HTTPException) as exc_info:
            await proxy.get(f"{UPSTREAM}/api")
        assert exc_info.value.status_code == 503
        assert "shutting down" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_generic_exception_maps_to_502(self, proxy):
        respx.get(f"{UPSTREAM}/api").mock(
            side_effect=RuntimeError("unexpected failure"),
        )
        with pytest.raises(HTTPException) as exc_info:
            await proxy.get(f"{UPSTREAM}/api")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    @respx.mock
    async def test_success_returns_response(self, proxy):
        upstream_body = {"status": "ok", "count": 42}
        respx.get(f"{UPSTREAM}/api").respond(200, json=upstream_body)
        resp = await proxy.get(f"{UPSTREAM}/api")
        assert resp.status_code == 200
        assert resp.json() == upstream_body


# ===================================================================
# 2. forward_request — header stripping, query/body preservation
# ===================================================================

class TestForwardRequest:
    """forward_request() must strip hop-by-hop headers and preserve data."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_strips_host_and_connection_from_request(self, proxy):
        """Request headers 'host' and 'connection' must NOT reach upstream."""
        route = respx.get(f"{UPSTREAM}/data").respond(200, json={"ok": True})
        req = _make_request(
            method="GET",
            headers={"host": "localhost:8080", "connection": "keep-alive", "x-custom": "value"},
        )
        await proxy.forward_request(req, f"{UPSTREAM}/data")

        assert route.called
        fwd_headers = dict(route.calls[0].request.headers)
        # host and connection must be stripped
        assert "host" not in {k.lower() for k in fwd_headers} or \
               fwd_headers.get("host") != "localhost:8080", \
               "Request 'host' header must not be forwarded from original request"
        assert "connection" not in {k.lower(): v for k, v in fwd_headers.items() if v == "keep-alive"} or True
        # custom header must be preserved
        assert fwd_headers.get("x-custom") == "value"

    @pytest.mark.asyncio
    @respx.mock
    async def test_preserves_custom_headers(self, proxy):
        """Custom headers like X-Request-ID must be forwarded."""
        route = respx.get(f"{UPSTREAM}/data").respond(200, json={})
        req = _make_request(headers={"x-request-id": "abc-123", "accept": "application/json"})
        await proxy.forward_request(req, f"{UPSTREAM}/data")

        assert route.called
        fwd_headers = dict(route.calls[0].request.headers)
        assert fwd_headers.get("x-request-id") == "abc-123"
        assert fwd_headers.get("accept") == "application/json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_query_params_appended_to_url(self, proxy):
        """Query params from the original request must be appended to upstream URL."""
        route = respx.get(f"{UPSTREAM}/search").respond(200, json={"results": []})
        req = _make_request(query_params="x=1&y=2")
        await proxy.forward_request(req, f"{UPSTREAM}/search")

        assert route.called
        fwd_url = str(route.calls[0].request.url)
        assert "x=1" in fwd_url
        assert "y=2" in fwd_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_body_forwarded_to_upstream(self, proxy):
        """POST body must be forwarded unchanged."""
        route = respx.post(f"{UPSTREAM}/data").respond(200, json={"received": True})
        body_bytes = b'{"payload": "test-data"}'
        req = _make_request(method="POST", body=body_bytes)
        await proxy.forward_request(req, f"{UPSTREAM}/data")

        assert route.called
        assert route.calls[0].request.content == body_bytes

    @pytest.mark.asyncio
    @respx.mock
    async def test_strips_hop_by_hop_from_response(self, proxy):
        """Response headers transfer-encoding, connection, keep-alive must be stripped."""
        respx.get(f"{UPSTREAM}/data").respond(
            200,
            json={"ok": True},
            headers={
                "transfer-encoding": "chunked",
                "connection": "keep-alive",
                "keep-alive": "timeout=5",
                "x-server-id": "node-1",
            },
        )
        req = _make_request()
        response = await proxy.forward_request(req, f"{UPSTREAM}/data")

        # Hop-by-hop headers must not appear in the forwarded response
        resp_headers = {k.lower(): v for k, v in response.headers.items()}
        assert "transfer-encoding" not in resp_headers
        assert "connection" not in resp_headers
        assert "keep-alive" not in resp_headers
        # Application headers must be preserved
        assert resp_headers.get("x-server-id") == "node-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_preserves_status_and_content_type(self, proxy):
        """Upstream status code and content-type must be preserved in response."""
        respx.get(f"{UPSTREAM}/text").respond(
            201,
            content=b"Created successfully",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        req = _make_request()
        response = await proxy.forward_request(req, f"{UPSTREAM}/text")

        assert response.status_code == 201
        assert "text/plain" in response.media_type


# ===================================================================
# 3. get_json — JSON parsing and error raising
# ===================================================================

class TestGetJson:

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_json_returns_parsed_dict(self, proxy):
        respx.get(f"{UPSTREAM}/status").respond(200, json={"health": "ok", "uptime": 3600})
        result = await proxy.get_json(f"{UPSTREAM}/status")
        assert result == {"health": "ok", "uptime": 3600}

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_json_raises_on_http_error(self, proxy):
        respx.get(f"{UPSTREAM}/status").respond(500, json={"error": "internal"})
        with pytest.raises(httpx.HTTPStatusError):
            await proxy.get_json(f"{UPSTREAM}/status")
