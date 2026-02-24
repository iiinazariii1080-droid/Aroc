"""Extra branch-coverage tests for frontend_service/main.py."""

import json
import os
import pytest
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch


@pytest.fixture
def _main(monkeypatch):
    svc_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    monkeypatch.chdir(svc_root)
    import main as mod
    return mod


@pytest.fixture
async def client(_main):
    transport = ASGITransport(app=_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── arm3d_v2 not built ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_arm3d_v2_not_built(client, _main):
    """When viewer/dist/index.html doesn't exist → 503."""
    with patch.object(Path, "exists", return_value=False):
        r = await client.get("/arm3d_v2")
    assert r.status_code == 503
    assert "not built" in r.json()["detail"]


# ── depth_map_load corrupted ────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_load_corrupted(client, _main, tmp_path, monkeypatch):
    """Saved depth map with bad header → 500 corrupted."""
    bin_path = tmp_path / "depth.bin"
    bin_path.write_bytes(b"BAD_DATA_SHORT")
    monkeypatch.setattr(_main, "DEPTH_MAP_BIN_PATH", bin_path)
    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 500
    assert "corrupted" in r.json()["detail"]


# ── depth_map_delete success ────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_delete_removes_files(client, _main, tmp_path, monkeypatch):
    bin_p = tmp_path / "depth.bin"
    meta_p = tmp_path / "depth.json"
    bin_p.write_bytes(b"x")
    meta_p.write_text("{}")
    monkeypatch.setattr(_main, "DEPTH_MAP_BIN_PATH", bin_p)
    monkeypatch.setattr(_main, "DEPTH_MAP_META_PATH", meta_p)
    r = await client.delete("/api/v1/depth_map")
    assert r.status_code == 200
    assert r.json()["removed"] is True


@pytest.mark.asyncio
async def test_depth_map_delete_nothing(client, _main, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "DEPTH_MAP_BIN_PATH", tmp_path / "nope.bin")
    monkeypatch.setattr(_main, "DEPTH_MAP_META_PATH", tmp_path / "nope.json")
    r = await client.delete("/api/v1/depth_map")
    assert r.status_code == 200
    assert r.json()["removed"] is False
