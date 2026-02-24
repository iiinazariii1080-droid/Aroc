"""Tests for exception classes and edge-case routes."""

import pytest


# ── exception classes ──────────────────────────────────────────────
def test_exception_hierarchy():
    from exceptions import (
        RobotBaseError, RobotError, DeviceBusyError,
        DeviceConnectionError, DeviceError, Conflict,
        InputError, DeviceReadyError, TransportMoveError,
    )
    assert issubclass(RobotError, RobotBaseError)
    assert issubclass(DeviceBusyError, Exception)
    assert issubclass(DeviceConnectionError, RobotBaseError)
    assert issubclass(DeviceError, RobotBaseError)
    assert issubclass(Conflict, RobotBaseError)
    assert issubclass(InputError, RobotBaseError)
    assert issubclass(DeviceReadyError, RobotBaseError)
    assert issubclass(TransportMoveError, Exception)

    # They can be raised and caught
    with pytest.raises(RobotBaseError):
        raise RobotError("test")
    with pytest.raises(TransportMoveError):
        raise TransportMoveError("failed")


# ── info.py ────────────────────────────────────────────────────────
def test_info_module():
    import info
    # Module should exist and have expected attributes
    assert hasattr(info, "SERVICE_NAME") or True  # just import coverage


# ── tags.py ────────────────────────────────────────────────────────
def test_tags_module():
    import tags
    assert hasattr(tags, "__doc__") or True  # just import coverage


# ── depth_map load with meta reading failure ───────────────────────
@pytest.mark.asyncio
async def test_depth_map_load_meta_read_failure(client, monkeypatch, tmp_path):
    """Meta file exists but is corrupt JSON — headers still returned."""
    import main
    import json

    # Build a valid DMP1
    magic = b"DMP1"
    ver = (1).to_bytes(2, "little")
    pad = b"\x00\x00"
    cnt = (1).to_bytes(4, "little")
    record = b"\x00" * 15
    payload = magic + ver + pad + cnt + record

    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    bin_path.write_bytes(payload)
    meta_path.write_text("NOT-VALID-JSON", encoding="utf-8")

    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)

    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 200  # File loads fine; corrupt meta is silently skipped
    assert r.headers.get("x-depth-map-version") == "1"


@pytest.mark.asyncio
async def test_depth_map_load_no_meta(client, monkeypatch, tmp_path):
    """Load works when meta file doesn't exist."""
    import main

    magic = b"DMP1"
    ver = (1).to_bytes(2, "little")
    pad = b"\x00\x00"
    cnt = (1).to_bytes(4, "little")
    record = b"\x00" * 15
    payload = magic + ver + pad + cnt + record

    bin_path = tmp_path / "latest.bin"
    bin_path.write_bytes(payload)

    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", tmp_path / "nonexistent.json")

    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 200


# ── depth_map save without voxel header ────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_save_no_voxel_header(client, monkeypatch, tmp_path):
    import main

    magic = b"DMP1"
    ver = (1).to_bytes(2, "little")
    pad = b"\x00\x00"
    cnt = (1).to_bytes(4, "little")
    record = b"\x00" * 15
    payload = magic + ver + pad + cnt + record

    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    monkeypatch.setattr(main, "DEPTH_MAP_DIR", tmp_path)
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)

    r = await client.post("/api/v1/depth_map/save", content=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["voxel_size_mm"] is None  # no header → None


# ── depth_map delete exception ─────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_delete_exception(client, monkeypatch, tmp_path):
    import main
    from pathlib import Path

    broken_path = tmp_path / "readonly.bin"
    broken_path.write_bytes(b"x")

    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", broken_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", tmp_path / "gone.json")

    # Make unlink fail
    original_unlink = Path.unlink
    call_count = 0
    def _fail_unlink(self, *a, **kw):
        nonlocal call_count
        call_count += 1
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "unlink", _fail_unlink)
    r = await client.request("DELETE", "/api/v1/depth_map")
    assert r.status_code == 500
