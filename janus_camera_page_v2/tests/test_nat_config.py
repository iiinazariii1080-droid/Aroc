"""T1: NAT config integration tests.

Covers load/save/cache/patch/render/credentials — the entire
nat_config.py module that had zero integration test coverage.

Risk addressed: R05 (High — marker patching RuntimeError), R12 (cache TOCTOU)
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_test_settings
from app.services.nat_config import (
    JanusNatConfig,
    generate_turn_credentials,
    load_nat_config,
    save_nat_config,
    render_nat_block,
    patch_janus_cfg_with_nat,
    NAT_BEGIN_MARKER,
    NAT_END_MARKER,
)


# ===================================================================
# 1. generate_turn_credentials — pure function, no mocks needed
# ===================================================================

class TestGenerateTurnCredentials:

    def test_username_format(self):
        """Username must be '<expiry>:<user>' with expiry in the future."""
        username, _ = generate_turn_credentials("secret", user="webrtc", ttl=86400)
        parts = username.split(":")
        assert len(parts) == 2, f"Expected 'expiry:user', got '{username}'"
        expiry = int(parts[0])
        assert expiry > time.time(), "Expiry must be in the future"
        assert parts[1] == "webrtc"

    def test_credential_is_valid_base64(self):
        """Credential must be decodable base64."""
        _, credential = generate_turn_credentials("secret", user="test")
        decoded = base64.b64decode(credential)
        assert len(decoded) == 20, "HMAC-SHA1 produces 20-byte digest"

    @patch("app.services.nat_config.time.time", return_value=1000000)
    def test_deterministic_with_frozen_time(self, _mock_time):
        """Same inputs + frozen time = same output (proves HMAC correctness)."""
        u1, c1 = generate_turn_credentials("mysecret", user="alice", ttl=3600)
        u2, c2 = generate_turn_credentials("mysecret", user="alice", ttl=3600)
        assert u1 == u2
        assert c1 == c2
        assert u1 == "1003600:alice"
        assert len(c1) > 10  # base64 of 20 bytes = 28 chars


# ===================================================================
# 2. load_nat_config — async, cache, fallback
# ===================================================================

class TestLoadNatConfig:

    @pytest.mark.asyncio
    async def test_defaults_when_no_file(self, tmp_path):
        """Color camera, no janus-nat.json → returns defaults."""
        settings = make_test_settings(tmp_path, camera_type="color_camera")
        with patch("app.services.nat_config.get_settings", return_value=settings):
            cfg = await load_nat_config()
        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478
        assert cfg.turn_type == "tcp"

    @pytest.mark.asyncio
    async def test_loads_from_disk(self, tmp_path):
        """Color camera with janus-nat.json on disk → parsed fields match."""
        nat_json = tmp_path / "janus-nat.json"
        nat_json.write_text(json.dumps({
            "stun_server": "stun.example.com",
            "stun_port": 3479,
            "turn_server": "turn.example.com",
            "turn_port": 5349,
        }))
        settings = make_test_settings(tmp_path, camera_type="color_camera", janus_nat_json=nat_json)
        with patch("app.services.nat_config.get_settings", return_value=settings):
            cfg = await load_nat_config()
        assert cfg.stun_server == "stun.example.com"
        assert cfg.stun_port == 3479
        assert cfg.turn_server == "turn.example.com"
        assert cfg.turn_port == 5349

    @pytest.mark.asyncio
    async def test_depth_camera_fetches_from_remote(self, tmp_path):
        """Depth camera fetches NAT config from color camera via HTTP."""
        settings = make_test_settings(tmp_path, camera_type="depth_camera")
        remote_data = {
            "stun_server": "remote-stun.example.com",
            "turn_server": "remote-turn.example.com",
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = remote_data
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.nat_config.get_settings", return_value=settings), \
             patch("app.services.nat_config._cross_node_proxy") as mock_proxy:
            mock_proxy.get = AsyncMock(return_value=mock_response)
            cfg = await load_nat_config()

        assert cfg.stun_server == "remote-stun.example.com"
        assert cfg.turn_server == "remote-turn.example.com"

    @pytest.mark.asyncio
    async def test_depth_camera_falls_back_to_disk(self, tmp_path):
        """Depth camera: remote unreachable → fallback to local file."""
        nat_json = tmp_path / "janus-nat.json"
        nat_json.write_text(json.dumps({"stun_server": "local-fallback.example.com"}))
        settings = make_test_settings(tmp_path, camera_type="depth_camera", janus_nat_json=nat_json)

        with patch("app.services.nat_config.get_settings", return_value=settings), \
             patch("app.services.nat_config._cross_node_proxy") as mock_proxy:
            mock_proxy.get = AsyncMock(side_effect=Exception("Connection refused"))
            cfg = await load_nat_config()

        assert cfg.stun_server == "local-fallback.example.com"

    @pytest.mark.asyncio
    async def test_cache_hit_within_ttl(self, tmp_path):
        """Second call within TTL returns cached object without re-fetching."""
        nat_json = tmp_path / "janus-nat.json"
        nat_json.write_text(json.dumps({"stun_server": "cached.example.com"}))
        settings = make_test_settings(tmp_path, camera_type="color_camera",
                                       janus_nat_json=nat_json, nat_config_ttl_sec=60.0)

        with patch("app.services.nat_config.get_settings", return_value=settings):
            cfg1 = await load_nat_config()
            # Modify the file — should NOT be re-read due to cache
            nat_json.write_text(json.dumps({"stun_server": "updated.example.com"}))
            cfg2 = await load_nat_config()

        assert cfg1 is cfg2, "Second call should return exact same cached object"
        assert cfg2.stun_server == "cached.example.com"


# ===================================================================
# 3. save_nat_config — file persistence + cache invalidation
# ===================================================================

class TestSaveNatConfig:

    def test_persists_json_without_turn_pwd(self, tmp_path):
        """Saved JSON must not contain turn_pwd (security invariant)."""
        nat_json = tmp_path / "janus-nat.json"
        settings = make_test_settings(tmp_path, janus_nat_json=nat_json)

        cfg = JanusNatConfig(
            stun_server="stun.test.com",
            turn_server="turn.test.com",
            turn_pwd="super-secret-password",
        )

        with patch("app.services.nat_config.get_settings", return_value=settings):
            save_nat_config(cfg)

        saved_data = json.loads(nat_json.read_text())
        assert "turn_pwd" not in saved_data, "turn_pwd must never be persisted to disk"
        assert saved_data["stun_server"] == "stun.test.com"
        assert saved_data["turn_server"] == "turn.test.com"

    def test_invalidates_cache(self, tmp_path):
        """save_nat_config() must invalidate the in-memory cache."""
        import app.services.nat_config as nc

        nat_json = tmp_path / "janus-nat.json"
        settings = make_test_settings(tmp_path, janus_nat_json=nat_json)

        # Pre-populate cache
        nc._nat_config_cache = JanusNatConfig()
        nc._nat_config_cache_ts = time.time()

        cfg = JanusNatConfig(stun_server="new.example.com")
        with patch("app.services.nat_config.get_settings", return_value=settings):
            save_nat_config(cfg)

        assert nc._nat_config_cache is None, "Cache must be invalidated after save"
        assert nc._nat_config_cache_ts == 0.0


# ===================================================================
# 4. render_nat_block — output format contract
# ===================================================================

class TestRenderNatBlock:

    def test_renders_all_fields_and_uses_env_turn_pass(self, monkeypatch):
        """NAT block must contain all config fields; TURN_PASS from env, not cfg."""
        monkeypatch.setenv("TURN_PASS", "env-secret-pwd")

        cfg = JanusNatConfig(
            stun_server="stun.render.test",
            stun_port=3478,
            turn_server="turn.render.test",
            turn_port=5349,
            turn_type="tls",
            turn_user="renderuser",
            turn_pwd="config-pwd-should-not-appear",
            nat_1_1_mapping="1.2.3.4",
            ice_tcp=True,
            full_trickle=False,
            keep_private_host=False,
            min_port=40000,
            max_port=41000,
        )

        block = render_nat_block(cfg)

        # Verify all fields present in output
        assert 'stun_server = "stun.render.test"' in block
        assert "stun_port   = 3478" in block
        assert 'turn_server = "turn.render.test"' in block
        assert "turn_port   = 5349" in block
        assert 'turn_type   = "tls"' in block
        assert 'turn_user   = "renderuser"' in block
        assert 'nat_1_1_mapping = "1.2.3.4"' in block
        assert "ice_tcp = true" in block
        assert "full_trickle = false" in block
        assert "keep_private_host = false" in block
        assert "min_port = 40000" in block
        assert "max_port = 41000" in block

        # Critical: TURN_PASS from env, not from cfg.turn_pwd
        assert 'turn_pwd    = "env-secret-pwd"' in block
        assert "config-pwd-should-not-appear" not in block


# ===================================================================
# 5. patch_janus_cfg_with_nat — marker-based patching
# ===================================================================

class TestPatchJanusCfgWithNat:

    def test_replaces_between_markers(self, tmp_path, monkeypatch):
        """Content between markers is replaced; surrounding text preserved."""
        monkeypatch.setenv("TURN_PASS", "test-pass")

        janus_cfg = tmp_path / "janus.jcfg"
        janus_cfg.write_text(
            "general: {\n  admin = true\n}\n\n"
            f"{NAT_BEGIN_MARKER}\n"
            "old nat content here\n"
            f"{NAT_END_MARKER}\n\n"
            "plugins: {\n  streaming = true\n}\n"
        )
        settings = make_test_settings(tmp_path, janus_cfg_path=janus_cfg)

        cfg = JanusNatConfig(
            stun_server="patched.stun.test",
            turn_server="patched.turn.test",
        )

        with patch("app.services.nat_config.get_settings", return_value=settings):
            patch_janus_cfg_with_nat(cfg)

        result = janus_cfg.read_text()

        # Surrounding text preserved
        assert "general: {" in result
        assert "admin = true" in result
        assert "plugins: {" in result
        assert "streaming = true" in result

        # Old content replaced
        assert "old nat content here" not in result

        # New NAT block present
        assert 'stun_server = "patched.stun.test"' in result
        assert 'turn_server = "patched.turn.test"' in result

        # Markers preserved
        assert NAT_BEGIN_MARKER in result
        assert NAT_END_MARKER in result

    def test_raises_on_missing_markers(self, tmp_path, monkeypatch):
        """Missing markers → RuntimeError (most likely production incident)."""
        janus_cfg = tmp_path / "janus.jcfg"
        janus_cfg.write_text(
            "general: {\n  admin = true\n}\n\n"
            "nat: {\n  stun_server = old\n}\n"
            # No markers!
        )
        settings = make_test_settings(tmp_path, janus_cfg_path=janus_cfg)
        cfg = JanusNatConfig()

        with patch("app.services.nat_config.get_settings", return_value=settings):
            with pytest.raises(RuntimeError, match="Markers"):
                patch_janus_cfg_with_nat(cfg)
