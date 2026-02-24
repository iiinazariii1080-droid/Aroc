"""Additional proxy tests covering main.py edge cases."""

import pytest
import respx
from httpx import Response


@pytest.fixture(autouse=True)
def _avoid_self_proxy(monkeypatch):
    """Ensure depth proxy doesn't trigger the self-proxy guard."""
    import main
    monkeypatch.setattr(main, "DEPTH_UPSTREAM", "http://127.0.0.1:9001/depth_camera")
    monkeypatch.setenv("SERVICE_PORT", "9000")


@pytest.mark.asyncio
async def test_janus_proxy_post(client):
    """POST to /janus proxied to JANUS_UPSTREAM."""
    from main import JANUS_UPSTREAM
    with respx.mock:
        respx.post(f"{JANUS_UPSTREAM}").mock(
            return_value=Response(200, json={"janus": "success"})
        )
        r = await client.post("/janus", json={"transaction": "abc"})
    assert r.status_code == 200
    assert r.json()["janus"] == "success"


@pytest.mark.asyncio
async def test_janus_proxy_subpath(client):
    """GET /janus/info proxied with subpath."""
    from main import JANUS_UPSTREAM
    with respx.mock:
        respx.get(f"{JANUS_UPSTREAM}/info").mock(
            return_value=Response(200, json={"janus": "server_info"})
        )
        r = await client.get("/janus/info")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_janus_proxy_timeout(client):
    """Janus upstream timeout returns 504."""
    import httpx as _httpx
    from main import JANUS_UPSTREAM
    with respx.mock:
        respx.get(f"{JANUS_UPSTREAM}/slow").mock(side_effect=_httpx.ReadTimeout("timeout"))
        r = await client.get("/janus/slow")
    assert r.status_code == 504


@pytest.mark.asyncio
async def test_janus_proxy_connect_error_502(client):
    """Janus upstream connection error returns 502."""
    import httpx as _httpx
    from main import JANUS_UPSTREAM
    with respx.mock:
        respx.get(f"{JANUS_UPSTREAM}/down").mock(side_effect=_httpx.ConnectError("refused"))
        r = await client.get("/janus/down")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_depth_proxy_post(client):
    """POST through depth proxy."""
    with respx.mock:
        respx.post("http://127.0.0.1:9001/depth_camera/api/data").mock(
            return_value=Response(201, json={"created": True})
        )
        r = await client.post("/depth_camera/api/data", json={"value": 1})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_depth_proxy_timeout(client):
    """Depth upstream timeout returns 504."""
    import httpx as _httpx
    with respx.mock:
        respx.get("http://127.0.0.1:9001/depth_camera/slow").mock(side_effect=_httpx.ReadTimeout("timeout"))
        r = await client.get("/depth_camera/slow")
    assert r.status_code == 504


@pytest.mark.asyncio
async def test_depth_proxy_connect_error(client):
    """Depth upstream connection error returns 502."""
    import httpx as _httpx
    with respx.mock:
        respx.get("http://127.0.0.1:9001/depth_camera/gone").mock(side_effect=_httpx.ConnectError("refused"))
        r = await client.get("/depth_camera/gone")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_depth_proxy_self_proxy_guard(client, monkeypatch):
    """Self-proxy guard raises 500."""
    import main
    monkeypatch.setattr(main, "DEPTH_UPSTREAM", "http://127.0.0.1:9000/depth_camera")
    monkeypatch.setenv("SERVICE_PORT", "9000")
    r = await client.get("/depth_camera/some_path")
    assert r.status_code == 500
    assert "self-proxy" in r.json().get("detail", "").lower()
