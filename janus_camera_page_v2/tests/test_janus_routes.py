"""Tests for app/routes/janus.py — Janus health, NAT, proxy."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_test_settings

_JR_TMP = Path(tempfile.mkdtemp(prefix="janus_routes_test_"))


def _client_cfg_settings(**overrides):
    """Create real Settings for /client-config tests."""
    defaults = dict(camera_type="color_camera", ice_policy="all", turn_shared_secret="", turn_cred_ttl=86400)
    defaults.update(overrides)
    return make_test_settings(_JR_TMP, **defaults)

from app.routes.janus import (
    JanusNatConfig,
    load_nat_config,
    restart_janus,
)
from app.services import nat_config
from app.services.nat_config import render_nat_block


class TestJanusHealthz:
    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.janus_summary", new_callable=AsyncMock)
    async def test_healthy(self, mock_summary, client):
        from app.routes.janus import JanusHealthResponse
        mock_summary.return_value = {
            "reachable": True,
            "mountpoint_id": 1,
            "enabled": True,
            "video_active": True,
            "video_age_ms": 50,
            "codec": "h264",
            "pt": None,
            "fmtp": None,
        }
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200
        parsed = JanusHealthResponse.model_validate(resp.json())
        assert parsed.ok is True

    @pytest.mark.asyncio
    @patch("app.routes.janus.janus.janus_summary", new_callable=AsyncMock, return_value={
        "reachable": False, "mountpoint_id": None, "enabled": None,
        "video_active": False, "video_age_ms": None, "codec": None,
        "pt": None, "fmtp": None,
    })
    async def test_janus_empty_response(self, mock_summary, client):
        from app.routes.janus import JanusHealthResponse
        resp = await client.get("/janus/healthz")
        assert resp.status_code == 200
        parsed = JanusHealthResponse.model_validate(resp.json())
        assert parsed.ok is False


class TestJanusRestart:
    @pytest.mark.asyncio
    @patch("app.routes.janus.restart_janus")
    async def test_restart_ok(self, mock_restart, client):
        # api_key may be None (no auth required) so omit header
        resp = await client.post("/janus/restart")
        assert resp.status_code == 200


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
        mock_settings.return_value = _client_cfg_settings()
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        # Full response schema validation
        assert isinstance(body["iceServers"], list)
        assert len(body["iceServers"]) >= 1
        for server in body["iceServers"]:
            assert "urls" in server, f"ICE server missing 'urls': {server}"
        assert body["sdpSemantics"] == "unified-plan"
        assert body["bundlePolicy"] == "balanced"
        assert body["rtcpMuxPolicy"] == "require"
        assert body["iceTransportPolicy"] in ("all", "relay")

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_stun_and_turn_servers_present(self, mock_settings, mock_nat, client):
        """Verify STUN and TURN URLs are correctly formed with credentials."""
        mock_settings.return_value = _client_cfg_settings()
        mock_nat.return_value = JanusNatConfig(
            stun_server="stun.contract.test",
            stun_port=3478,
            turn_server="turn.contract.test",
            turn_port=5349,
            turn_type="tcp",
            turn_user="testuser",
            turn_pwd="testpass",
        )
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()

        all_urls = []
        for server in body["iceServers"]:
            urls = server["urls"]
            if isinstance(urls, str):
                all_urls.append(urls)
            else:
                all_urls.extend(urls)

        # STUN server present
        stun_urls = [u for u in all_urls if u.startswith("stun:")]
        assert len(stun_urls) >= 1, f"No STUN URL found in {all_urls}"
        assert any("stun.contract.test" in u for u in stun_urls)

        # TURN server present with credentials
        turn_entries = [s for s in body["iceServers"] if any(
            u.startswith("turn:") or u.startswith("turns:")
            for u in (s["urls"] if isinstance(s["urls"], list) else [s["urls"]])
        )]
        assert len(turn_entries) >= 1, f"No TURN entry found in {body['iceServers']}"
        turn = turn_entries[0]
        assert "username" in turn, "TURN entry must have username"
        assert "credential" in turn, "TURN entry must have credential"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_depth_camera_forces_relay_policy(self, mock_settings, mock_nat, client):
        """Depth camera behind double NAT must always return iceTransportPolicy=relay."""
        mock_settings.return_value = _client_cfg_settings(camera_type="depth_camera")
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "relay"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_color_camera_respects_env_policy(self, mock_settings, mock_nat, client):
        """Color camera uses the ICE_POLICY env var as-is."""
        mock_settings.return_value = _client_cfg_settings()
        mock_nat.return_value = JanusNatConfig()
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        assert resp.json()["iceTransportPolicy"] == "all"

    @pytest.mark.asyncio
    @patch("app.routes.janus.load_nat_config")
    @patch("app.routes.janus.get_settings")
    async def test_ephemeral_turn_creds_when_shared_secret_set(self, mock_settings, mock_nat, client):
        """When TURN_SHARED_SECRET is set, /client-config returns time-limited HMAC credentials."""
        mock_settings.return_value = _client_cfg_settings(
            turn_shared_secret="test-secret-abc", turn_cred_ttl=3600,
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
    def _nat_settings(self, cam_type: str, nat_json_path):
        import tempfile
        from pathlib import Path
        from tests.conftest import make_test_settings
        _tmp = Path(tempfile.mkdtemp(prefix="nat_settings_"))
        return make_test_settings(_tmp, camera_type=cam_type, janus_nat_json=nat_json_path)

    @pytest.mark.asyncio
    async def test_defaults_when_no_file(self, tmp_path):
        nat_json = tmp_path / "janus-nat.json"
        # File does not exist → defaults are returned
        with patch("app.services.nat_config.get_settings",
                   return_value=self._nat_settings("color_camera", nat_json)), \
             patch("app.services.nat_config._nat_config_cache", None), \
             patch("app.services.nat_config._nat_config_cache_ts", 0.0):
            cfg = await load_nat_config()
        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478

    @pytest.mark.asyncio
    async def test_reads_from_json_file(self, tmp_path):
        nat_json = tmp_path / "janus-nat.json"
        nat_json.write_text(json.dumps({"stun_server": "1.2.3.4", "stun_port": 9999}))
        with patch("app.services.nat_config.get_settings",
                   return_value=self._nat_settings("color_camera", nat_json)), \
             patch("app.services.nat_config._nat_config_cache", None), \
             patch("app.services.nat_config._nat_config_cache_ts", 0.0):
            cfg = await load_nat_config()
        assert cfg.stun_server == "1.2.3.4"
        assert cfg.stun_port == 9999

    @pytest.mark.asyncio
    async def test_depth_camera_fetches_remote(self, tmp_path):
        nat_json = tmp_path / "janus-nat.json"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "5.6.7.8"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.services.nat_config.get_settings",
                   return_value=self._nat_settings("depth_camera", nat_json)), \
             patch("app.services.nat_config._nat_config_cache", None), \
             patch("app.services.nat_config._nat_config_cache_ts", 0.0), \
             patch.object(nat_config._cross_node_proxy, "get", new_callable=AsyncMock, return_value=mock_resp):
            cfg = await load_nat_config()
        assert cfg.stun_server == "5.6.7.8"


class TestRenderNatBlock:
    def test_renders_template(self):
        cfg = JanusNatConfig()
        block = render_nat_block(cfg)
        assert "stun_server" in block
        assert "turn_server" in block
        assert "nat_1_1_mapping" in block
        assert "nat:" in block


class TestRestartJanus:
    @patch("app.utils.process.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        restart_janus()
        mock_run.assert_called_once()

    @patch("app.utils.process.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error", stdout="")
        with pytest.raises(RuntimeError, match="Failed to restart"):
            restart_janus()
