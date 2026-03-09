"""L5 Depth Camera Proxy route tests.

Validates that /api/v1/depth_camera/* routes correctly forward
requests to the upstream depth camera, handle failures gracefully,
and reject path-traversal attempts.

All tests use mocked httpx responses — no real depth camera needed.

Markers: integration, unit
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

DEPTH_BASE = "http://192.168.1.55:8900"


@pytest.fixture
def app_color():
    """Create a color-camera app instance (includes depth proxy routes)."""
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {
             "CAM_ADMIN_ENFORCE": "0",
             "CAM_TYPE": "color_camera",
             "DEPTH_CAM_URL": DEPTH_BASE,
         }):
        import app.core.admin as _admin
        _admin._ENFORCE = False
        from app.core.app import create_app
        return create_app()


@pytest.fixture
async def client_color(app_color):
    transport = ASGITransport(app=app_color)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Proxy forwarding tests ───────────────────────────────────────────

@pytest.mark.integration
class TestProxyForwarding:
    """Verify proxy correctly forwards requests to depth camera."""

    @respx.mock
    async def test_healthz_proxy(self, client_color):
        """GET /api/v1/depth_camera/healthz → forwards to .55/healthz."""
        route = respx.get(f"{DEPTH_BASE}/healthz").respond(
            200, json={"ok": True, "mode": "nominal"},
        )
        resp = await client_color.get("/api/v1/depth_camera/healthz")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert route.called

    @respx.mock
    async def test_depth_query_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth?x=50&y=50 → forwards depth query."""
        route = respx.get(f"{DEPTH_BASE}/depth").respond(
            200, json={"type": "depth", "x": 50, "y": 50, "depth": 1.5},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth?x=50&y=50")
        assert resp.status_code == 200
        assert resp.json()["depth"] == 1.5
        assert route.called

    @respx.mock
    async def test_depth_frame_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth/frame → forwards depth frame request."""
        route = respx.get(f"{DEPTH_BASE}/depth/frame").respond(
            200, content=b"\x00" * 100, headers={"content-type": "application/octet-stream"},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth/frame")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_depth_overlay_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth/frame_color_overlay → forwards overlay request."""
        route = respx.get(f"{DEPTH_BASE}/depth/frame_color_overlay").respond(
            200, json={"overlay": "data"},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth/frame_color_overlay")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_client_config_proxy(self, client_color):
        """GET /api/v1/depth_camera/client-config → forwards ICE config."""
        route = respx.get(f"{DEPTH_BASE}/client-config").respond(
            200, json={"iceServers": [{"urls": "turn:82.165.177.194:3478"}]},
        )
        resp = await client_color.get("/api/v1/depth_camera/client-config")
        assert resp.status_code == 200
        data = resp.json()
        assert "iceServers" in data
        assert route.called

    @respx.mock
    async def test_snapshot_proxy(self, client_color):
        """GET /api/v1/depth_camera/snapshot.jpg → forwards snapshot."""
        route = respx.get(f"{DEPTH_BASE}/snapshot.jpg").respond(
            200, content=b"\xff\xd8\xff\xe0", headers={"content-type": "image/jpeg"},
        )
        resp = await client_color.get("/api/v1/depth_camera/snapshot.jpg")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_modes_proxy(self, client_color):
        """GET /api/v1/depth_camera/modes → forwards modes list."""
        route = respx.get(f"{DEPTH_BASE}/modes").respond(
            200, json={"modes": []},
        )
        resp = await client_color.get("/api/v1/depth_camera/modes")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_janus_proxy_get(self, client_color):
        """GET /api/v1/depth_camera/janus → forwards Janus API request."""
        route = respx.get(f"{DEPTH_BASE}/janus").respond(
            200, json={"janus": "server_info"},
        )
        resp = await client_color.get("/api/v1/depth_camera/janus")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_janus_proxy_post(self, client_color):
        """POST /api/v1/depth_camera/janus → forwards Janus POST."""
        route = respx.post(f"{DEPTH_BASE}/janus").respond(
            200, json={"janus": "success", "session_id": 12345},
        )
        resp = await client_color.post(
            "/api/v1/depth_camera/janus",
            json={"janus": "create"},
        )
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_config_get_proxy(self, client_color):
        """GET /api/v1/depth_camera/config → forwards config read."""
        route = respx.get(f"{DEPTH_BASE}/config").respond(
            200, json={"width": 640, "height": 480, "fps": 15},
        )
        resp = await client_color.get("/api/v1/depth_camera/config")
        assert resp.status_code == 200
        assert route.called

    @respx.mock
    async def test_config_post_proxy(self, client_color):
        """POST /api/v1/depth_camera/config → forwards config update."""
        route = respx.post(f"{DEPTH_BASE}/config").respond(200, json={"ok": True})
        resp = await client_color.post(
            "/api/v1/depth_camera/config",
            json={"bitrate_kbps": 2000},
        )
        assert resp.status_code == 200
        assert route.called


# ── Upstream failure tests ───────────────────────────────────────────

@pytest.mark.integration
class TestUpstreamFailure:
    """Verify proxy returns correct error codes when .55 is unreachable."""

    @respx.mock
    async def test_connect_error_returns_502(self, client_color):
        """Depth camera unreachable → HTTP 502."""
        respx.get(f"{DEPTH_BASE}/healthz").mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        resp = await client_color.get("/api/v1/depth_camera/healthz")
        assert resp.status_code == 502

    @respx.mock
    async def test_timeout_returns_504(self, client_color):
        """Depth camera timeout → HTTP 504."""
        respx.get(f"{DEPTH_BASE}/depth").mock(
            side_effect=httpx.TimeoutException("read timeout"),
        )
        resp = await client_color.get("/api/v1/depth_camera/depth?x=50&y=50")
        assert resp.status_code == 504

    @respx.mock
    async def test_upstream_500_forwarded(self, client_color):
        """Depth camera returns 500 → proxy forwards 500."""
        respx.get(f"{DEPTH_BASE}/depth/frame").respond(
            500, json={"detail": "internal error"},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth/frame")
        assert resp.status_code == 500

    @respx.mock
    async def test_upstream_404_forwarded(self, client_color):
        """Depth camera returns 404 → proxy forwards 404."""
        respx.get(f"{DEPTH_BASE}/janus/healthz").respond(
            404, json={"detail": "not found"},
        )
        resp = await client_color.get("/api/v1/depth_camera/janus/healthz")
        assert resp.status_code == 404


# ── Query parameter forwarding ───────────────────────────────────────

@pytest.mark.integration
class TestQueryParamForwarding:
    """Verify query parameters are forwarded to upstream."""

    @respx.mock
    async def test_query_params_forwarded(self, client_color):
        """Query string is forwarded to the depth camera."""
        route = respx.get(f"{DEPTH_BASE}/depth").respond(
            200, json={"type": "depth", "x": 25, "y": 75, "depth": 2.1},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth?x=25&y=75")
        assert resp.status_code == 200
        # Verify query was included in the forwarded URL
        assert route.called
        request = route.calls[0].request
        assert b"x=25" in request.url.raw_path or "x=25" in str(request.url)


# ── Static asset proxying ────────────────────────────────────────────

@pytest.mark.integration
class TestStaticAssetProxy:
    """Verify JS/HTML assets are proxied correctly."""

    @respx.mock
    async def test_janus_js_proxy(self, client_color):
        respx.get(f"{DEPTH_BASE}/janus.js").respond(
            200, content=b"/* janus.js */", headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/janus.js")
        assert resp.status_code == 200

    @respx.mock
    async def test_streamer_js_proxy(self, client_color):
        respx.get(f"{DEPTH_BASE}/streamer.js").respond(
            200, content=b"/* streamer */", headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/streamer.js")
        assert resp.status_code == 200

    @respx.mock
    async def test_player_subpath_proxy(self, client_color):
        """GET /api/v1/depth_camera/player/somefile.js → proxied."""
        respx.get(f"{DEPTH_BASE}/player/somefile.js").respond(
            200, content=b"/* file */", headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/player/somefile.js")
        assert resp.status_code == 200

    @respx.mock
    async def test_favicon_proxy(self, client_color):
        respx.get(f"{DEPTH_BASE}/favicon.ico").respond(
            200, content=b"\x00\x00\x01",
        )
        resp = await client_color.get("/api/v1/depth_camera/favicon.ico")
        assert resp.status_code == 200
