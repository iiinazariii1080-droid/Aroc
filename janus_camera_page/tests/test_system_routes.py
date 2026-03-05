"""Tests for app/routes/system.py — system/health/static routes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_structured_response(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        # Deep healthz: in test env Janus is unavailable, so ok may be False
        assert "ok" in body
        assert "mode" in body
        assert "janus_reachable" in body
        assert "stream_active" in body
        assert "details" in body
        assert isinstance(body["ok"], bool)


class TestRelayEndpoints:
    @pytest.mark.asyncio
    @patch("app.services.relay_proxy.relay_get", return_value={"time": 12345})
    async def test_relay_time(self, mock_get, client):
        resp = await client.get("/relay/time")
        assert resp.status_code == 200

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


class TestRestartService:
    @pytest.mark.asyncio
    @patch("app.routes.system.service_restart")
    async def test_restart_ok(self, mock_restart, client):
        # Default settings has api_key=None → no auth required
        resp = await client.post("/action/restart")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_restart.assert_called_once()


class TestJanusJsServing:
    @pytest.mark.asyncio
    async def test_serves_local_file(self, client, tmp_path):
        js_file = tmp_path / "janus.js"
        js_file.write_text("// janus lib")
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/janus.js")
        assert resp.status_code == 200


class TestStreamerJsServing:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/streamer.js")
        assert resp.status_code == 404


class TestDepthFeaturesJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/depth_features.js")
        assert resp.status_code == 404


class TestGamepadJs:
    @pytest.mark.asyncio
    async def test_missing_returns_404(self, client):
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/gamepaddriver.js")
        assert resp.status_code == 404


class TestGamepadConfig:
    @pytest.mark.asyncio
    async def test_serves_json(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text('{"axes": [0, 1]}')
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 200
        assert resp.json()["axes"] == [0, 1]

    @pytest.mark.asyncio
    async def test_invalid_json_500(self, client, tmp_path):
        cfg = tmp_path / "gamepad_config.json"
        cfg.write_text("{invalid")
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/gamepad_config.json")
        assert resp.status_code == 500


class TestPlayerScript:
    @pytest.mark.asyncio
    async def test_serves_player_file(self, client, tmp_path):
        player_dir = tmp_path / "player"
        player_dir.mkdir()
        (player_dir / "app.js").write_text("// app")
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/app.js")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_path_traversal(self, client, tmp_path):
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/player/../../../etc/passwd")
        assert resp.status_code in (400, 403, 404)


class TestFavicon:
    @pytest.mark.asyncio
    async def test_favicon_missing(self, client):
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir="/nonexistent", camera_type="rgb_camera")
            resp = await client.get("/favicon.ico")
        assert resp.status_code in (404, 204)


class TestColorView:
    @pytest.mark.asyncio
    async def test_template_render(self, client, tmp_path):
        html = tmp_path / "color_view.html"
        html.write_text("<html>__CAM_TYPE__</html>")
        with patch("app.routes.system.get_settings") as mock_s:
            mock_s.return_value = MagicMock(templates_dir=str(tmp_path), camera_type="rgb_camera")
            resp = await client.get("/color_view.html")
        assert resp.status_code == 200
        assert "rgb_camera" in resp.text


class TestDepthMapLoad:
    """Tests for /api/v1/depth_map/load and /depth_map/load proxy routes."""

    @pytest.mark.asyncio
    @patch("app.routes.system.get_settings")
    async def test_depth_camera_node_proxies_locally(self, mock_settings, client):
        """On a depth_camera node the request proxies to localhost:8000/depth_map."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
        )
        fake_payload = {"width": 480, "height": 848, "dtype": "float32", "timestamp": 1.0, "data": "AAAA"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = fake_payload

        with patch("requests.get", return_value=mock_resp) as mock_get:
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 200
        assert resp.json()["width"] == 480
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert "localhost:8000/depth_map" in call_url

    @pytest.mark.asyncio
    @patch("app.routes.system.get_settings")
    async def test_color_camera_node_proxies_remote(self, mock_settings, client):
        """On a color_camera node the request proxies to the depth camera URL."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            depth_cam_url="http://192.168.1.55:8900",
        )
        fake_payload = {"width": 480, "height": 848, "dtype": "float32", "timestamp": 1.0, "data": "AAAA"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = fake_payload

        with patch("requests.get", return_value=mock_resp) as mock_get:
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 200
        assert resp.json()["dtype"] == "float32"
        call_url = mock_get.call_args[0][0]
        assert "192.168.1.55:8900" in call_url

    @pytest.mark.asyncio
    @patch("app.routes.system.get_settings")
    async def test_upstream_error_returns_502(self, mock_settings, client):
        """Network failure to upstream returns 502."""
        import requests as _req

        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            depth_cam_url="http://192.168.1.55:8900",
        )
        with patch("requests.get", side_effect=_req.ConnectionError("refused")) as _:
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 502
        assert "Depth map proxy error" in resp.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.routes.system.get_settings")
    async def test_upstream_503_forwarded(self, mock_settings, client):
        """Upstream 503 (no frame yet) is forwarded."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
        )
        mock_resp = MagicMock(status_code=503, text='{"detail":"no depth frame yet"}')
        with patch("requests.get", return_value=mock_resp):
            resp = await client.get("/depth_map/load")

        assert resp.status_code == 503

    @pytest.mark.asyncio
    @patch("app.routes.system.get_settings")
    async def test_raw_format_passthrough(self, mock_settings, client):
        """format=raw proxies binary content with headers."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            depth_cam_url="http://192.168.1.55:8900",
        )
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
        with patch("requests.get", return_value=mock_resp) as mock_get:
            resp = await client.get("/depth_map/load?format=raw")

        assert resp.status_code == 200
        assert resp.headers["x-width"] == "4"
        assert resp.headers["x-dtype"] == "float32"
        call_url = mock_get.call_args[0][0]
        assert "format=raw" in call_url
