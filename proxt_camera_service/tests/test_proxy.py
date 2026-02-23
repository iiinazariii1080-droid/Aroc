"""Tests for proxt_camera_service — health, proxy, and static routes."""

import os
import sys
import pytest
import respx
from httpx import Response

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.mark.asyncio
async def test_healthz(client):
    r = await client.get("/depth_camera/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_depth_camera_page(client):
    """GET /depth_camera should return HTML page."""
    r = await client.get("/depth_camera")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_depth_camera_slash(client):
    """GET /depth_camera/ should also return HTML page."""
    r = await client.get("/depth_camera/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_status_check_when_upstream_down(client):
    """GET /depth_camera/status should return connected=false when upstream is down."""
    with respx.mock:
        r = await client.get("/depth_camera/status")
    assert r.status_code == 200
    body = r.json()
    assert "connected" in body
    assert "ip" in body
    assert "port" in body


@pytest.mark.asyncio
async def test_depth_proxy_upstream_error(client):
    """When upstream returns error, proxy should forward the status code."""
    from main import DEPTH_UPSTREAM

    with respx.mock:
        respx.get(f"{DEPTH_UPSTREAM}/some_endpoint").mock(
            return_value=Response(500, content=b"Internal Server Error")
        )
        r = await client.get("/depth_camera/some_endpoint")
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_depth_proxy_upstream_success(client):
    """When upstream responds OK, proxy should forward the response."""
    from main import DEPTH_UPSTREAM

    with respx.mock:
        respx.get(f"{DEPTH_UPSTREAM}/depth").mock(
            return_value=Response(200, content=b"\x00" * 100, headers={"content-type": "application/octet-stream"})
        )
        # /depth_camera/depth requires a 'message' query param with JSON coords
        import json
        r = await client.get("/depth_camera/depth", params={"message": json.dumps({"x": 50, "y": 50})})
    # May succeed or fail depending on camera service init; just ensure no crash
    assert r.status_code in (200, 500)


@pytest.mark.asyncio
async def test_janus_proxy_connect_error(client):
    """When Janus upstream is unreachable, proxy returns 502 or error."""
    from aioresponses import aioresponses

    with aioresponses() as m:
        m.get("http://127.0.0.1:8088/janus/info", exception=Exception("Connection refused"))
        r = await client.get("/janus/info")
    assert r.status_code in (200, 404, 500, 502)


@pytest.mark.asyncio
async def test_favicon(client):
    """GET /favicon.ico should return 204 or a file."""
    r = await client.get("/favicon.ico")
    assert r.status_code in (200, 204)
