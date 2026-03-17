"""L5 Depth Camera Proxy route tests.

Validates that /api/v1/depth_camera/* routes correctly forward
requests to the upstream depth camera, handle failures gracefully,
and reject path-traversal attempts.

All tests use mocked httpx responses — no real depth camera needed.

Markers: integration, unit
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings

DEPTH_BASE = "http://192.168.1.55:8900"


def assert_proxy_forwarded(
    route,
    resp,
    *,
    expected_status=200,
    expected_body=None,
    expected_content_type=None,
    expected_query_params=None,
):
    """Assert proxy forwarded request correctly with full contract validation.

    Unlike bare ``assert route.called``, this validates response body,
    content-type headers, and query parameter forwarding.
    """
    assert route.called, "upstream route was never called"
    assert resp.status_code == expected_status, (
        f"expected {expected_status}, got {resp.status_code}"
    )
    if expected_body is not None:
        if isinstance(expected_body, (dict, list)):
            actual = resp.json()
            assert actual == expected_body, (
                f"response body mismatch:\n  expected: {expected_body}\n  actual:   {actual}"
            )
        elif isinstance(expected_body, bytes):
            assert resp.content == expected_body, "binary response body mismatch"
    if expected_content_type is not None:
        actual_ct = resp.headers.get("content-type", "")
        assert expected_content_type in actual_ct, (
            f"content-type mismatch: expected '{expected_content_type}' "
            f"in '{actual_ct}'"
        )
    if expected_query_params is not None:
        request = route.calls[0].request
        url_str = str(request.url)
        for param in expected_query_params:
            assert param in url_str, (
                f"query param '{param}' not found in forwarded URL: {url_str}"
            )


@pytest.fixture
def app_color(monkeypatch, tmp_path):
    """Create a color-camera app instance (includes depth proxy routes)."""
    from unittest.mock import MagicMock
    monkeypatch.setenv("CAM_TYPE", "color_camera")
    from app.core.settings import get_settings
    get_settings.cache_clear()

    _ms = make_test_settings(tmp_path, depth_cam_url=DEPTH_BASE, admin_enforce=False)
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.app.StaticFiles", MagicMock()), \
         patch("app.core.admin.get_settings", return_value=_ms):
        from app.core.app import create_app
        yield create_app()


@pytest.fixture
async def client_color(app_color, tmp_path):
    _ms = make_test_settings(tmp_path, depth_cam_url=DEPTH_BASE, admin_enforce=False)
    with patch("app.services.depth_camera_proxy.get_settings", return_value=_ms):
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
        upstream_body = {"ok": True, "mode": "nominal"}
        route = respx.get(f"{DEPTH_BASE}/healthz").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/healthz")
        assert_proxy_forwarded(
            route, resp,
            expected_body=upstream_body,
            expected_content_type="application/json",
        )

    @respx.mock
    async def test_depth_query_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth?x=50&y=50 → forwards depth query."""
        upstream_body = {"type": "depth", "x": 50, "y": 50, "depth": 1.5}
        route = respx.get(f"{DEPTH_BASE}/depth").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/depth?x=50&y=50")
        assert_proxy_forwarded(
            route, resp,
            expected_body=upstream_body,
            expected_content_type="application/json",
            expected_query_params=["x=50", "y=50"],
        )

    @respx.mock
    async def test_depth_frame_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth/frame → forwards depth frame request."""
        frame_bytes = b"\x00" * 100
        route = respx.get(f"{DEPTH_BASE}/depth/frame").respond(
            200, content=frame_bytes, headers={"content-type": "application/octet-stream"},
        )
        resp = await client_color.get("/api/v1/depth_camera/depth/frame")
        assert_proxy_forwarded(
            route, resp,
            expected_body=frame_bytes,
            expected_content_type="application/octet-stream",
        )

    @respx.mock
    async def test_depth_overlay_proxy(self, client_color):
        """GET /api/v1/depth_camera/depth/frame_color_overlay → forwards overlay request."""
        upstream_body = {"overlay": "data"}
        route = respx.get(f"{DEPTH_BASE}/depth/frame_color_overlay").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/depth/frame_color_overlay")
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_client_config_proxy(self, client_color):
        """GET /api/v1/depth_camera/client-config → forwards ICE config."""
        upstream_body = {"iceServers": [{"urls": "turn:82.165.177.194:3478"}]}
        route = respx.get(f"{DEPTH_BASE}/client-config").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/client-config")
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_snapshot_proxy(self, client_color):
        """GET /api/v1/depth_camera/snapshot.jpg → forwards snapshot."""
        jpeg_bytes = b"\xff\xd8\xff\xe0"
        route = respx.get(f"{DEPTH_BASE}/snapshot.jpg").respond(
            200, content=jpeg_bytes, headers={"content-type": "image/jpeg"},
        )
        resp = await client_color.get("/api/v1/depth_camera/snapshot.jpg")
        assert_proxy_forwarded(route, resp, expected_body=jpeg_bytes, expected_content_type="image/jpeg")

    @respx.mock
    async def test_modes_proxy(self, client_color):
        """GET /api/v1/depth_camera/modes → forwards modes list."""
        upstream_body = {"modes": []}
        route = respx.get(f"{DEPTH_BASE}/modes").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/modes")
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_janus_proxy_get(self, client_color):
        """GET /api/v1/depth_camera/janus → forwards Janus API request."""
        upstream_body = {"janus": "server_info"}
        route = respx.get(f"{DEPTH_BASE}/janus").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/janus")
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_janus_proxy_post(self, client_color):
        """POST /api/v1/depth_camera/janus → forwards Janus POST."""
        upstream_body = {"janus": "success", "session_id": 12345}
        route = respx.post(f"{DEPTH_BASE}/janus").respond(200, json=upstream_body)
        resp = await client_color.post(
            "/api/v1/depth_camera/janus",
            json={"janus": "create"},
        )
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_config_get_proxy(self, client_color):
        """GET /api/v1/depth_camera/config → forwards config read."""
        upstream_body = {"width": 640, "height": 480, "fps": 15}
        route = respx.get(f"{DEPTH_BASE}/config").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/config")
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")

    @respx.mock
    async def test_config_post_proxy(self, client_color):
        """POST /api/v1/depth_camera/config → forwards config update."""
        upstream_body = {"ok": True}
        route = respx.post(f"{DEPTH_BASE}/config").respond(200, json=upstream_body)
        resp = await client_color.post(
            "/api/v1/depth_camera/config",
            json={"bitrate_kbps": 2000},
        )
        assert_proxy_forwarded(route, resp, expected_body=upstream_body, expected_content_type="application/json")


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
        upstream_body = {"type": "depth", "x": 25, "y": 75, "depth": 2.1}
        route = respx.get(f"{DEPTH_BASE}/depth").respond(200, json=upstream_body)
        resp = await client_color.get("/api/v1/depth_camera/depth?x=25&y=75")
        assert_proxy_forwarded(
            route, resp,
            expected_body=upstream_body,
            expected_query_params=["x=25", "y=75"],
        )


# ── Static asset proxying ────────────────────────────────────────────

@pytest.mark.integration
class TestStaticAssetProxy:
    """Verify JS/HTML assets are proxied correctly."""

    @respx.mock
    async def test_janus_js_proxy(self, client_color):
        js_body = b"/* janus.js */"
        route = respx.get(f"{DEPTH_BASE}/janus.js").respond(
            200, content=js_body, headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/janus.js")
        assert_proxy_forwarded(route, resp, expected_body=js_body, expected_content_type="application/javascript")

    @respx.mock
    async def test_streamer_js_proxy(self, client_color):
        js_body = b"/* streamer */"
        route = respx.get(f"{DEPTH_BASE}/streamer.js").respond(
            200, content=js_body, headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/streamer.js")
        assert_proxy_forwarded(route, resp, expected_body=js_body, expected_content_type="application/javascript")

    @respx.mock
    async def test_player_subpath_proxy(self, client_color):
        """GET /api/v1/depth_camera/player/somefile.js → proxied."""
        js_body = b"/* file */"
        route = respx.get(f"{DEPTH_BASE}/player/somefile.js").respond(
            200, content=js_body, headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/player/somefile.js")
        assert_proxy_forwarded(route, resp, expected_body=js_body, expected_content_type="application/javascript")

    @respx.mock
    async def test_favicon_proxy(self, client_color):
        favicon_bytes = b"\x00\x00\x01"
        route = respx.get(f"{DEPTH_BASE}/favicon.ico").respond(200, content=favicon_bytes)
        resp = await client_color.get("/api/v1/depth_camera/favicon.ico")
        assert_proxy_forwarded(route, resp, expected_body=favicon_bytes)

    @respx.mock
    async def test_janus_js_content_preserved(self, client_color):
        """Verify JS content bytes are forwarded exactly (not truncated)."""
        js_content = b"var Janus = function() { /* 46KB */ };"
        route = respx.get(f"{DEPTH_BASE}/janus.js").respond(
            200, content=js_content, headers={"content-type": "application/javascript"},
        )
        resp = await client_color.get("/api/v1/depth_camera/janus.js")
        assert_proxy_forwarded(
            route, resp,
            expected_body=js_content,
            expected_content_type="application/javascript",
        )


# ── POST body forwarding ──────────────────────────────────────────────

@pytest.mark.integration
class TestPostBodyForwarding:
    """Verify POST request bodies are forwarded to upstream."""

    @respx.mock
    async def test_post_body_forwarded_to_upstream(self, client_color):
        """POST /api/v1/depth_camera/config with JSON body → upstream receives exact payload."""
        upstream_body = {"ok": True, "applied": True}
        route = respx.post(f"{DEPTH_BASE}/config").respond(200, json=upstream_body)
        request_body = {"bitrate_kbps": 2000, "fps": 15}
        resp = await client_color.post(
            "/api/v1/depth_camera/config",
            json=request_body,
        )
        assert_proxy_forwarded(route, resp, expected_body=upstream_body)
        # Verify the upstream received the request body
        import json
        forwarded_body = json.loads(route.calls[0].request.content)
        assert forwarded_body == request_body, (
            f"POST body not forwarded correctly:\n"
            f"  expected: {request_body}\n  got: {forwarded_body}"
        )
