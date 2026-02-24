"""Tests for depth-map CRUD endpoints and header parser."""

import json
import struct
import pytest


# ── helper: build a valid DMP1 payload ─────────────────────────────
def _build_dmp1(version: int = 1, count: int = 2) -> bytes:
    """Build a minimal DMP1 binary payload."""
    magic = b"DMP1"
    ver = version.to_bytes(2, "little")
    pad = b"\x00\x00"
    cnt = count.to_bytes(4, "little")
    header = magic + ver + pad + cnt         # 12 bytes
    record = b"\x00" * 15                    # 15-byte record stub
    return header + (record * count)


# ── _parse_depth_map_header ────────────────────────────────────────
def test_parse_header_valid():
    import main
    payload = _build_dmp1(version=2, count=3)
    ver, cnt = main._parse_depth_map_header(payload)
    assert ver == 2
    assert cnt == 3


def test_parse_header_too_small():
    import main
    with pytest.raises(ValueError, match="too small"):
        main._parse_depth_map_header(b"DMP1short")


def test_parse_header_bad_magic():
    import main
    bad = b"XXXX" + b"\x00" * 20
    with pytest.raises(ValueError, match="invalid magic"):
        main._parse_depth_map_header(bad)


def test_parse_header_size_mismatch():
    import main
    payload = _build_dmp1(count=2) + b"\xFF"  # extra byte
    with pytest.raises(ValueError, match="mismatch"):
        main._parse_depth_map_header(payload)


# ── _ensure_depth_map_dir ──────────────────────────────────────────
def test_ensure_depth_map_dir(monkeypatch, tmp_path):
    import main
    target = tmp_path / "data" / "dm"
    monkeypatch.setattr(main, "DEPTH_MAP_DIR", target)
    main._ensure_depth_map_dir()
    assert target.is_dir()


# ── POST /api/v1/depth_map/save ───────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_save_success(client, monkeypatch, tmp_path):
    import main
    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    monkeypatch.setattr(main, "DEPTH_MAP_DIR", tmp_path)
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)

    payload = _build_dmp1(version=1, count=2)
    r = await client.post(
        "/api/v1/depth_map/save",
        content=payload,
        headers={"x-depth-map-voxel-size-mm": "5.0"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "dmp1"
    assert body["point_count"] == 2
    assert body["voxel_size_mm"] == 5.0
    assert bin_path.exists()
    assert meta_path.exists()


@pytest.mark.asyncio
async def test_depth_map_save_empty_body(client):
    r = await client.post("/api/v1/depth_map/save", content=b"")
    assert r.status_code == 400
    assert "Empty" in r.json()["detail"]


@pytest.mark.asyncio
async def test_depth_map_save_bad_magic(client):
    r = await client.post("/api/v1/depth_map/save", content=b"XXXX" + b"\x00" * 20)
    assert r.status_code == 400
    assert "invalid magic" in r.json()["detail"].lower()


# ── GET /api/v1/depth_map/info ─────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_info_not_found(client, monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", tmp_path / "nofile.bin")
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", tmp_path / "nofile.json")
    r = await client.get("/api/v1/depth_map/info")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_depth_map_info_success(client, monkeypatch, tmp_path):
    import main
    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    payload = _build_dmp1(version=1, count=1)
    bin_path.write_bytes(payload)
    meta = {"format": "dmp1", "version": 1, "point_count": 1}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)
    r = await client.get("/api/v1/depth_map/info")
    assert r.status_code == 200
    body = r.json()
    assert body["point_count"] == 1
    assert "bytes" in body  # auto-added by info endpoint


@pytest.mark.asyncio
async def test_depth_map_info_corrupt_json(client, monkeypatch, tmp_path):
    import main
    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    bin_path.write_bytes(b"\x00")
    meta_path.write_text("NOT JSON", encoding="utf-8")
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)
    r = await client.get("/api/v1/depth_map/info")
    assert r.status_code == 500


# ── GET /api/v1/depth_map/load ─────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_load_not_found(client, monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", tmp_path / "no.bin")
    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_depth_map_load_success(client, monkeypatch, tmp_path):
    import main
    payload = _build_dmp1(version=1, count=2)
    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    bin_path.write_bytes(payload)
    meta = {"saved_at": "2026-01-01T00:00:00Z"}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)
    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 200
    assert r.headers.get("x-depth-map-version") == "1"
    assert r.headers.get("x-depth-map-point-count") == "2"
    assert r.content == payload


@pytest.mark.asyncio
async def test_depth_map_load_corrupt(client, monkeypatch, tmp_path):
    import main
    bin_path = tmp_path / "latest.bin"
    bin_path.write_bytes(b"DMP1" + b"\x01\x00\x00\x00" + b"\x05\x00\x00\x00")  # count=5 but no records
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    r = await client.get("/api/v1/depth_map/load")
    assert r.status_code == 500
    assert "corrupt" in r.json()["detail"].lower()


# ── DELETE /api/v1/depth_map ───────────────────────────────────────
@pytest.mark.asyncio
async def test_depth_map_delete_removes(client, monkeypatch, tmp_path):
    import main
    bin_path = tmp_path / "latest.bin"
    meta_path = tmp_path / "latest.meta.json"
    bin_path.write_bytes(b"x")
    meta_path.write_text("{}")
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", bin_path)
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", meta_path)

    r = await client.request("DELETE", "/api/v1/depth_map")
    assert r.status_code == 200
    assert r.json()["removed"] is True
    assert not bin_path.exists()
    assert not meta_path.exists()


@pytest.mark.asyncio
async def test_depth_map_delete_already_gone(client, monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "DEPTH_MAP_BIN_PATH", tmp_path / "gone.bin")
    monkeypatch.setattr(main, "DEPTH_MAP_META_PATH", tmp_path / "gone.json")
    r = await client.request("DELETE", "/api/v1/depth_map")
    assert r.status_code == 200
    assert r.json()["removed"] is False
