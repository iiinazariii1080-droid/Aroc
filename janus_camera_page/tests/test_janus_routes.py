"""Tests for app/routes/janus.py and app/services/nat_config.py — Janus health, NAT, proxy."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes.janus import JanusNatConfig
from app.services.nat_config import (
    load_nat_config,
    render_nat_block,
    restart_depth_camera_janus,
    restart_janus,
)


class TestJanusHealthz:
    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.streaming_info")
    async def test_healthy(self, mock_info, client):
        mock_info.return_value = {
            "data": {
                "info": {
                    "info": {
                        "id": 1,
                        "enabled": True,
                        "media": [{"age_ms": 50, "codec": "h264"}],
                    }
                }
            }
        }
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.streaming_info", return_value={})
    async def test_janus_empty_response(self, mock_info, client):
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False


class TestJanusRestart:
    @pytest.mark.asyncio
    @patch("app.routes.janus.restart_janus")
    async def test_restart_ok(self, mock_restart, client):
        resp = await client.post("/janus/restart")
        # No admin token provided → require_admin raises 403
        assert resp.status_code == 403


class TestJanusProxy:
    @pytest.mark.asyncio
    @patch("app.services.janus_proxy.forward_request")
    async def test_proxy_get(self, mock_fwd, client):
        from fastapi.responses import Response

        mock_fwd.return_value = Response(content=b'{"janus":"pong"}', media_type="application/json")
        resp = await client.get("/janus")
        assert resp.status_code == 200


class TestClientConfig:
    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_returns_config(self, mock_settings, mock_nat, client, settings):
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        assert "iceServers" in body

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_depth_camera_forces_relay_policy(self, mock_settings, mock_nat, client):
        """Depth camera behind double NAT must always return iceTransportPolicy=relay."""
        mock_settings.return_value = MagicMock(
            camera_type="depth_camera",
            ice_policy="all",  # env says "all", but depth must override to "relay"
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "relay"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_color_camera_respects_env_policy(self, mock_settings, mock_nat, client):
        """Color camera uses the ICE_POLICY env var as-is."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="",
            turn_cred_ttl=86400,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "all"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_ephemeral_turn_creds_when_shared_secret_set(self, mock_settings, mock_nat, client):
        """When TURN_SHARED_SECRET is set, /client-config returns time-limited HMAC credentials."""
        mock_settings.return_value = MagicMock(
            camera_type="color_camera",
            ice_policy="all",
            turn_shared_secret="test-secret-abc",
            turn_cred_ttl=3600,
        )
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        # TURN server entry should have ephemeral username (timestamp:user format)
        turn_entry = [s for s in body["iceServers"] if s.get("username")]
        assert len(turn_entry) == 1
        assert ":" in turn_entry[0]["username"]  # "expiry:webrtc"
        assert len(turn_entry[0]["credential"]) > 10  # base64 HMAC


# ── Helper function tests (no HTTP client needed) ───────────────────


class TestLoadNatConfig:
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_defaults_when_no_file(self, mock_settings, mock_path):
        mock_settings.return_value = MagicMock(camera_type="rgb_camera")
        mock_path.return_value.exists.return_value = False
        cfg = load_nat_config()
        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478

    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_reads_from_json_file(self, mock_settings, mock_path):
        mock_settings.return_value = MagicMock(camera_type="rgb_camera")
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_text.return_value = json.dumps({"stun_server": "1.2.3.4", "stun_port": 9999})
        cfg = load_nat_config()
        assert cfg.stun_server == "1.2.3.4"
        assert cfg.stun_port == 9999

    @patch("app.services.nat_config.httpx.get")
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_depth_camera_fetches_remote(self, mock_settings, mock_path, mock_get):
        mock_settings.return_value = MagicMock(camera_type="depth_camera")
        mock_path.return_value.exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "5.6.7.8"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        cfg = load_nat_config()
        assert cfg.stun_server == "5.6.7.8"

    @patch("app.services.nat_config.ADMIN_TOKEN", "test-admin-token-def01")
    @patch("app.services.nat_config.httpx.get")
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_depth_camera_sends_admin_token(self, mock_settings, mock_path, mock_get):
        """DEF-01: depth camera must send X-Admin-Token when fetching NAT config."""
        mock_settings.return_value = MagicMock(camera_type="depth_camera")
        mock_path.return_value.exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "1.2.3.4"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        load_nat_config()
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["X-Admin-Token"] == "test-admin-token-def01"


class TestRestartDepthCameraJanus:
    """DEF-01: restart_depth_camera_janus must send X-Admin-Token."""

    @patch("app.services.nat_config.ADMIN_TOKEN", "test-admin-token-def01")
    @patch("app.services.nat_config.httpx.post")
    def test_sends_admin_token(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        restart_depth_camera_janus()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Admin-Token"] == "test-admin-token-def01"

    @patch("app.services.nat_config.ADMIN_TOKEN", "test-admin-token-def01")
    @patch("app.services.nat_config.httpx.post")
    def test_raises_on_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="error")
        with pytest.raises(RuntimeError, match="Failed to restart janus"):
            restart_depth_camera_janus()


class TestRenderNatBlock:
    def test_renders_template_with_ignore_list(self):
        """Default config uses ice_ignore_list (blacklist) for multi-homed TURN safety."""
        cfg = JanusNatConfig()
        block = render_nat_block(cfg)
        assert "stun_server" in block
        assert "turn_server" in block
        assert "nat_1_1_mapping" in block
        assert "nat:" in block
        assert "ice_ignore_list" in block
        assert "ice_enforce_list" not in block
        assert '"docker"' in block
        assert '"tailscale"' in block

    def test_renders_enforce_list_when_set(self):
        """When ice_enforce_list is explicitly set, render it instead of ignore list."""
        cfg = JanusNatConfig(ice_enforce_list="br0")
        block = render_nat_block(cfg)
        assert 'ice_enforce_list = "br0"' in block
        assert "ice_ignore_list" not in block

    def test_renders_custom_ignore_list(self):
        """Custom ice_ignore_list entries are rendered correctly."""
        cfg = JanusNatConfig(ice_ignore_list=["lo", "wg0"])
        block = render_nat_block(cfg)
        assert 'ice_ignore_list = [ "lo", "wg0" ]' in block


class TestRestartJanus:
    @patch("app.services.nat_config.run_cmd")
    def test_success(self, mock_run):
        restart_janus()
        mock_run.assert_called_once()

    @patch("app.services.nat_config.run_cmd", side_effect=RuntimeError("cmd failed"))
    def test_failure(self, mock_run):
        with pytest.raises(RuntimeError):
            restart_janus()


class TestJanusHealthzDefensive:
    """Test defensive parsing in janus_healthz."""

    @pytest.mark.asyncio
    async def test_healthz_malformed_response(self, client):
        with patch("app.routes.janus.janus.streaming_info", return_value="garbage"):
            resp = await client.get("/janus/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is False

    @pytest.mark.asyncio
    async def test_healthz_valid_response(self, client):
        with patch("app.routes.janus.janus.streaming_info", return_value={
            "data": {"info": {"id": 1, "enabled": True}}
        }):
            resp = await client.get("/janus/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
