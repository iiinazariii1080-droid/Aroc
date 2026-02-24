"""Tests for app/routes/janus.py — Janus health, NAT, proxy."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes.janus import (
    JanusNatConfig,
    load_nat_config,
    render_nat_block,
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
    async def test_returns_config(self, mock_nat, client, settings):
        mock_nat.return_value = MagicMock(
            stun_server=None,
            turn_server=None,
            ice_policy="all",
            model_dump=lambda exclude_none=True: {"ice_policy": "all"},
        )
        resp = await client.get("/client-config")
        assert resp.status_code == 200
        body = resp.json()
        assert "janus_ws" in body or "iceServers" in body or "janus_url" in body


# ── Helper function tests (no HTTP client needed) ───────────────────


class TestLoadNatConfig:
    @patch("app.routes.janus.JANUS_NAT_JSON")
    @patch("app.routes.janus.CAM_TYPE", "rgb_camera")
    def test_defaults_when_no_file(self, mock_path):
        mock_path.exists.return_value = False
        cfg = load_nat_config()
        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478

    @patch("app.routes.janus.JANUS_NAT_JSON")
    @patch("app.routes.janus.CAM_TYPE", "rgb_camera")
    def test_reads_from_json_file(self, mock_path):
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({"stun_server": "1.2.3.4", "stun_port": 9999})
        cfg = load_nat_config()
        assert cfg.stun_server == "1.2.3.4"
        assert cfg.stun_port == 9999

    @patch("app.routes.janus.requests.get")
    @patch("app.routes.janus.JANUS_NAT_JSON")
    @patch("app.routes.janus.CAM_TYPE", "depth_camera")
    def test_depth_camera_fetches_remote(self, mock_path, mock_get):
        mock_path.exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stun_server": "5.6.7.8"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        cfg = load_nat_config()
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
    @patch("app.routes.janus.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        restart_janus()
        mock_run.assert_called_once()

    @patch("app.routes.janus.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error", stdout="")
        with pytest.raises(RuntimeError, match="Failed to restart"):
            restart_janus()
