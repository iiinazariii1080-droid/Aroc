"""Tests for app/routes/system.py — system/health/static routes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_structured_response(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        # Full schema validation with types
        assert isinstance(body["ok"], bool)
        assert isinstance(body["mode"], str)
        assert body["mode"] in ("nominal", "degraded", "local_only", "safe")
        assert isinstance(body["janus_reachable"], bool)
        assert isinstance(body["stream_active"], bool)
        assert isinstance(body["details"], dict)

    @pytest.mark.asyncio
    @patch("app.routes.system.janus.janus_summary")
    async def test_healthz_unhealthy_when_janus_unreachable(self, mock_summary, client):
        """When Janus is unreachable, /healthz returns ok=False."""
        from unittest.mock import AsyncMock
        mock_summary.side_effect = Exception("Janus connection refused")
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["janus_reachable"] is False
        assert body["stream_active"] is False


class TestRelayEndpoints:
    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", return_value={"time": 12345})
    async def test_relay_time(self, mock_get, client):
        resp = await client.get("/relay/time")
        assert resp.status_code == 200
        assert resp.json() == {"time": 12345}

    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", side_effect=Exception("down"))
    async def test_relay_time_error(self, mock_get, client):
        resp = await client.get("/relay/time")
        assert resp.status_code == 502

    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", return_value={"pong": True})
    async def test_relay_pong(self, mock_get, client):
        resp = await client.get("/relay/pong")
        assert resp.status_code == 200
        assert resp.json() == {"pong": True}


class TestRestartService:
    @pytest.mark.asyncio
    @patch("app.routes.system.service_restart")
    async def test_restart_ok(self, mock_restart, client):
        from app.routes.system import ActionResponse
        # Default settings has api_key=None → no auth required
        resp = await client.post("/action/restart")
        assert resp.status_code == 200
        parsed = ActionResponse.model_validate(resp.json())
        assert parsed.ok is True
        mock_restart.assert_called_once()


class TestJanusJsServing:
    @pytest.mark.asyncio
    async def test_serves_local_file(self, client, tmp_path):
        js_file = tmp_path / "janus.js"
        js_file.write_text("// janus lib")
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/janus.js")
        assert resp.status_code == 200


class TestStreamerJsServing:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/streamer.js")
        assert resp.status_code == 404


class TestDepthFeaturesJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/depth_features.js")
        assert resp.status_code == 404


class TestGamepadJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/gamepaddriver.js")
        assert resp.status_code == 404


class TestGamepadConfig:
    @pytest.mark.asyncio
    async def test_serves_json(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text('{"axes": [0, 1]}')
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 200
        assert resp.json()["axes"] == [0, 1]

    @pytest.mark.asyncio
    async def test_invalid_json_500(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text("{invalid")
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 500


class TestPlayerScript:
    @pytest.mark.asyncio
    async def test_serves_player_file(self, client, tmp_path):
        player_dir = tmp_path / "player"
        player_dir.mkdir()
        (player_dir / "app.js").write_text("// app")
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/app.js")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_path_traversal(self, client, tmp_path):
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/../../../etc/passwd")
        assert resp.status_code in (400, 403, 404)


class TestFavicon:
    @pytest.mark.asyncio
    async def test_favicon_missing(self, client):
        with patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", static_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/favicon.ico")
        assert resp.status_code in (404, 204)


class TestColorView:
    @pytest.mark.asyncio
    async def test_template_render(self, client, tmp_path):
        html = tmp_path / "color_view.html"
        html.write_text("<html>{{ cam_type }}</html>")
        from fastapi.templating import Jinja2Templates
        fake_jinja = Jinja2Templates(directory=str(tmp_path))
        with patch("app.routes.media.get_jinja", return_value=fake_jinja), \
             patch("app.routes.media.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        assert "rgb_camera" in resp.text


class TestDepthMapLoad:
    """Tests for /depth_map/load proxy route.

    The endpoint lives on ``depth.router`` (depth_camera nodes) and
    ``depth_proxy.router`` (color_camera nodes).  We use a dedicated
    depth_camera app fixture to test the depth_camera variant.
    """

    @pytest.fixture
    def depth_app(self):
        """Create a test app with CAM_TYPE=depth_camera."""
        import os
        old = os.environ.get("CAM_TYPE")
        os.environ["CAM_TYPE"] = "depth_camera"
        from app.core.settings import get_settings
        get_settings.cache_clear()
        try:
            with patch("app.core.events.register_event_handlers", lambda app: None), \
                 patch("app.core.admin.get_settings", return_value=MagicMock(admin_enforce=False, admin_token="test-token")):
                from app.core.app import create_app
                yield create_app()
        finally:
            if old is None:
                os.environ.pop("CAM_TYPE", None)
            else:
                os.environ["CAM_TYPE"] = old
            get_settings.cache_clear()

    @pytest.fixture
    async def depth_client(self, depth_app):
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=depth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_depth_camera_node_proxies_locally(self, depth_client):
        """On a depth_camera node the request proxies via realsense_mux_proxy."""
        from unittest.mock import AsyncMock
        fake_payload = {"width": 480, "height": 848, "dtype": "float32", "timestamp": 1.0, "data": "AAAA"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = fake_payload

        with patch("app.services.realsense_mux_proxy.get", new=AsyncMock(return_value=mock_resp)):
            resp = await depth_client.get("/depth_map/load")

        assert resp.status_code == 200
        assert resp.json()["width"] == 480

    @pytest.mark.asyncio
    async def test_upstream_503_forwarded(self, depth_client):
        """Upstream 503 (no frame yet) is forwarded."""
        from unittest.mock import AsyncMock
        mock_resp = MagicMock(status_code=503, text='{"detail":"no depth frame yet"}')

        with patch("app.services.realsense_mux_proxy.get", new=AsyncMock(return_value=mock_resp)):
            resp = await depth_client.get("/depth_map/load")

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_raw_format_passthrough(self, depth_client):
        """format=raw proxies binary content with headers."""
        from unittest.mock import AsyncMock
        mock_resp = MagicMock(
            status_code=200,
            content=b"\x00" * 16,
            headers={
                "X-Width": "4",
                "X-Height": "1",
                "X-Dtype": "float32",
                "X-Timestamp": "1.0",
            },
        )

        with patch("app.services.realsense_mux_proxy.get", new=AsyncMock(return_value=mock_resp)):
            resp = await depth_client.get("/depth_map/load?format=raw")

        assert resp.status_code == 200
        assert resp.headers["x-width"] == "4"
        assert resp.headers["x-dtype"] == "float32"
