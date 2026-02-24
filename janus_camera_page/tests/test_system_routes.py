"""Tests for app/routes/system.py — system/health/static routes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_ok(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


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
