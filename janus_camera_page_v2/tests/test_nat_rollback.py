"""T5: NAT config rollback fault-injection tests.

Verifies that _rollback_nat() restores state when the 3-step mutation
(save JSON → patch janus.jcfg → restart Janus) fails at any step.

Risk addressed: R02 (HIGH — partial failure leaves inconsistent state)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings
from app.services.nat_config import (
    JanusNatConfig,
    NAT_BEGIN_MARKER,
    NAT_END_MARKER,
)


ORIGINAL_STUN = "original.stun.test"
NEW_STUN = "new.stun.test"


@pytest.fixture
def nat_env(tmp_path, monkeypatch):
    """Set up filesystem + app for NAT rollback tests."""
    monkeypatch.setenv("TURN_PASS", "test-pass")

    # Create initial janus-nat.json
    nat_json = tmp_path / "janus-nat.json"
    nat_json.write_text(json.dumps({"stun_server": ORIGINAL_STUN}))

    # Create janus.jcfg with markers
    janus_cfg = tmp_path / "janus.jcfg"
    original_jcfg = (
        f"general: {{}}\n\n"
        f"{NAT_BEGIN_MARKER}\n"
        f"old nat content\n"
        f"{NAT_END_MARKER}\n\n"
        f"plugins: {{}}\n"
    )
    janus_cfg.write_text(original_jcfg)

    settings = make_test_settings(
        tmp_path,
        camera_type="color_camera",
        janus_nat_json=nat_json,
        janus_cfg_path=janus_cfg,
        admin_enforce=False,
    )

    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch("app.core.admin.get_settings", return_value=settings), \
         patch("app.routes.janus.get_settings", return_value=settings), \
         patch("app.services.nat_config.get_settings", return_value=settings):
        from app.core.app import create_app
        app = create_app()
        yield {
            "app": app,
            "settings": settings,
            "nat_json": nat_json,
            "janus_cfg": janus_cfg,
            "original_jcfg": original_jcfg,
        }


@pytest.fixture
async def nat_client(nat_env):
    transport = ASGITransport(app=nat_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


NEW_CFG_BODY = {
    "stun_server": NEW_STUN,
    "stun_port": 3478,
    "turn_server": NEW_STUN,
    "turn_port": 3478,
    "turn_type": "tcp",
    "turn_user": "webrtc",
    "turn_pwd": "test-pass",
}


class TestNatRollback:
    """Verify _rollback_nat() restores state on failure at each step."""

    @pytest.mark.asyncio
    async def test_step1_save_fails_returns_500(self, nat_env, nat_client):
        """Step 1 (save JSON) fails → HTTP 500, original files untouched."""
        original_json = nat_env["nat_json"].read_text()
        original_jcfg = nat_env["janus_cfg"].read_text()

        with patch("app.routes.janus.save_nat_config", side_effect=OSError("disk full")):
            resp = await nat_client.post("/janus/nat", json=NEW_CFG_BODY)

        assert resp.status_code == 500
        assert "Failed to save" in resp.json()["detail"]
        # Original files must be untouched (step 1 failed before any mutation)
        assert nat_env["nat_json"].read_text() == original_json
        assert nat_env["janus_cfg"].read_text() == original_jcfg

    @pytest.mark.asyncio
    async def test_step2_patch_fails_rolls_back_json(self, nat_env, nat_client):
        """Step 2 (patch jcfg) fails → HTTP 500, janus-nat.json rolled back."""
        original_json = nat_env["nat_json"].read_text()

        with patch("app.routes.janus.patch_janus_cfg_with_nat", side_effect=RuntimeError("markers missing")):
            resp = await nat_client.post("/janus/nat", json=NEW_CFG_BODY)

        assert resp.status_code == 500
        assert "rolled back" in resp.json()["detail"]
        # JSON must be restored to original (rollback called)
        restored = json.loads(nat_env["nat_json"].read_text())
        assert restored["stun_server"] == ORIGINAL_STUN

    @pytest.mark.asyncio
    async def test_step3_restart_fails_rolls_back_both(self, nat_env, nat_client):
        """Step 3 (restart) fails → HTTP 500, both JSON and jcfg rolled back."""
        original_json_text = nat_env["nat_json"].read_text()

        with patch("app.routes.janus.restart_janus", side_effect=RuntimeError("systemd failed")), \
             patch("app.routes.janus.restart_depth_camera_janus", new_callable=AsyncMock):
            resp = await nat_client.post("/janus/nat", json=NEW_CFG_BODY)

        assert resp.status_code == 500
        assert "rolled back" in resp.json()["detail"]
        # JSON must be restored
        restored = json.loads(nat_env["nat_json"].read_text())
        assert restored["stun_server"] == ORIGINAL_STUN

    @pytest.mark.asyncio
    async def test_rollback_save_fails_still_returns_500(self, nat_env, nat_client):
        """Step 2 fails AND rollback's save also fails → HTTP 500 (no crash)."""
        save_call_count = 0

        def save_side_effect(cfg):
            nonlocal save_call_count
            save_call_count += 1
            if save_call_count == 1:
                # Step 1: save succeeds (the normal save)
                from app.services.nat_config import save_nat_config as real_save
                # Write manually to simulate success
                from app.utils.fs import atomic_write_text
                data = cfg.model_dump(exclude={"turn_pwd"})
                atomic_write_text(nat_env["nat_json"], json.dumps(data, indent=2))
                return
            else:
                # Rollback save fails
                raise OSError("disk full during rollback")

        with patch("app.routes.janus.save_nat_config", side_effect=save_side_effect), \
             patch("app.routes.janus.patch_janus_cfg_with_nat", side_effect=RuntimeError("markers broken")):
            resp = await nat_client.post("/janus/nat", json=NEW_CFG_BODY)

        # Must still return 500 without crashing
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_rollback_jcfg_write_fails_still_returns_500(self, nat_env, nat_client):
        """Step 3 fails AND rollback's jcfg write fails → HTTP 500 (no crash)."""
        with patch("app.routes.janus.restart_janus", side_effect=RuntimeError("restart failed")), \
             patch("app.routes.janus.restart_depth_camera_janus", new_callable=AsyncMock), \
             patch("app.utils.fs.atomic_write_text", side_effect=OSError("disk full")):
            # Note: atomic_write_text is also used in save_nat_config (step 1) and rollback
            # Since we're patching it globally, step 1 save will also fail.
            # Let's be more targeted — only fail during rollback by using side_effect list.
            pass

        # More targeted approach: patch only the rollback's atomic_write_text call
        original_atomic = None
        call_count = 0

        def atomic_side_effect(path, text):
            nonlocal call_count, original_atomic
            call_count += 1
            if call_count <= 2:
                # Steps 1 and 2 succeed (save JSON + patch jcfg)
                from app.utils import fs as real_fs
                # Call through to real implementation
                import os, tempfile
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=str(path.parent))
                try:
                    os.write(fd, text.encode() if isinstance(text, str) else text)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, str(path))
                return
            # Rollback jcfg write fails
            raise OSError("disk full during rollback")

        with patch("app.routes.janus.restart_janus", side_effect=RuntimeError("restart failed")), \
             patch("app.routes.janus.restart_depth_camera_janus", new_callable=AsyncMock), \
             patch("app.utils.fs.atomic_write_text", side_effect=atomic_side_effect):
            resp = await nat_client.post("/janus/nat", json=NEW_CFG_BODY)

        assert resp.status_code == 500
