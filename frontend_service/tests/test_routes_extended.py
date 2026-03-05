"""Tests for proxy routing, SPA fallback, static file routes, and utility functions."""

import pytest
import respx
from httpx import Response
from unittest.mock import patch


# ── SPA fallback edge cases ────────────────────────────────────────
@pytest.mark.asyncio
async def test_spa_fallback_api_prefix_404(client):
    """spa_fallback returns 404 for api/* paths."""
    import main
    r = await main.spa_fallback("api/test")
    assert r.status_code == 404
    assert b"Not Found" in r.body


@pytest.mark.asyncio
async def test_spa_fallback_static_prefix_404(client):
    """Static mount path traversal returns 404."""
    r = await client.get("/static/../../../etc/passwd")
    assert r.status_code == 404


# ── static file routes ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_map_viewer(client):
    r = await client.get("/map_viewer")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_arm3d_viewer(client):
    r = await client.get("/arm3d_viewer")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_arm3d_v2_not_built(client, monkeypatch):
    """When viewer dist doesn't exist, returns 503."""
    import main
    with patch("main.Path.exists", return_value=False):
        r = await client.get("/arm3d_v2")
    assert r.status_code == 503
    assert "not built" in r.json()["detail"]


# ── proxy API content-type handling ────────────────────────────────
@pytest.mark.asyncio
async def test_proxy_api_html_response(client):
    """HTML upstream response returned as HTMLResponse."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/docs").mock(
            return_value=Response(200, html="<h1>Docs</h1>")
        )
        r = await client.get("/api/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_proxy_api_binary_response(client):
    """Non-JSON non-HTML response passed through raw."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/file/data.bin").mock(
            return_value=Response(200, content=b"\x00\x01\x02",
                                 headers={"content-type": "application/octet-stream"})
        )
        r = await client.get("/api/file/data.bin")
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_proxy_api_put(client):
    """PUT method proxied correctly."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.put(f"{API_GATEWAY_URL.rstrip('/')}/api/settings/theme").mock(
            return_value=Response(200, json={"updated": True})
        )
        r = await client.put("/api/settings/theme", json={"dark": True})
    assert r.status_code == 200
    assert r.json()["updated"] is True


@pytest.mark.asyncio
async def test_proxy_api_delete(client):
    """DELETE method proxied correctly."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.delete(f"{API_GATEWAY_URL.rstrip('/')}/api/item/42").mock(
            return_value=Response(204)
        )
        r = await client.request("DELETE", "/api/item/42")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_proxy_api_invalid_json_response(client):
    """Upstream returns content-type json but body is not valid JSON."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/broken").mock(
            return_value=Response(200, content=b"not-json",
                                 headers={"content-type": "application/json"})
        )
        r = await client.get("/api/broken")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body  # "Invalid JSON response"


@pytest.mark.asyncio
async def test_proxy_api_with_query_params(client):
    """Query params forwarded to upstream."""
    from config import API_GATEWAY_URL
    with respx.mock:
        respx.get(f"{API_GATEWAY_URL.rstrip('/')}/api/search?q=hello").mock(
            return_value=Response(200, json={"results": []})
        )
        r = await client.get("/api/search", params={"q": "hello"})
    assert r.status_code == 200


# ── _ssl_ctx_for ───────────────────────────────────────────────────
def test_ssl_ctx_for_ws():
    """Non-SSL URL returns None."""
    import main
    assert main._ssl_ctx_for("ws://localhost:8188/janus-ws") is None


def test_ssl_ctx_for_wss():
    """WSS URL returns SSL context."""
    import main
    ctx = main._ssl_ctx_for("wss://secure.host/ws")
    assert ctx is not None


def test_ssl_ctx_for_wss_insecure(monkeypatch):
    """With ALLOW_INSECURE_TLS, SSL context has no hostname check."""
    import ssl
    import main
    monkeypatch.setattr(main, "ALLOW_INSECURE_TLS", True)
    ctx = main._ssl_ctx_for("wss://example.com/ws")
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE
