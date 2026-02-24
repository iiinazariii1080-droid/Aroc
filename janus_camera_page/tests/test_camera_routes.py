"""Tests for app/routes/camera.py — camera configuration routes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGetCameraModes:
    @pytest.mark.asyncio
    @patch("app.routes.camera.list_v4l2_modes")
    async def test_returns_modes(self, mock_modes, client):
        mock_modes.return_value = {
            "pixel_format": "YUYV",
            "device": "/dev/video0",
            "modes": [{"width": 640, "height": 480, "fps": [30, 25]}],
        }
        resp = await client.get("/modes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pixel_format"] == "YUYV"
        assert len(body["modes"]) == 1

    @pytest.mark.asyncio
    @patch("app.routes.camera.list_v4l2_modes", return_value={"modes": []})
    async def test_v4l2_empty_modes(self, mock_modes, client):
        resp = await client.get("/modes")
        assert resp.status_code == 200
        assert resp.json()["modes"] == []


class TestGetCameraStreamConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, client, tmp_path):
        env_file = tmp_path / "cam-rgb.env"
        env_file.write_text(
            'WIDTH="640"\nHEIGHT="480"\nFPS="30"\n'
            'BITRATE_KBPS="1800"\nPRESET="veryfast"\nTUNE="zerolatency"\n'
            'SNAPSHOT_FPS="1"\nPORT="5004"\n'
        )
        with patch("app.routes.camera.CAM_ENV_PATH", env_file):
            resp = await client.get("/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 640
        assert body["fps"] == 30

    @pytest.mark.asyncio
    async def test_defaults_when_env_missing(self, client, tmp_path):
        missing = tmp_path / "nonexistent.env"
        with patch("app.routes.camera.CAM_ENV_PATH", missing):
            resp = await client.get("/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 640  # default


class TestUpdateCameraStreamConfig:
    @pytest.mark.asyncio
    @patch("app.routes.camera.restart_rtp_rgb")
    async def test_update_success(self, mock_restart, client, tmp_path):
        env_file = tmp_path / "cam-rgb.env"
        env_file.write_text('WIDTH="640"\nHEIGHT="480"\nFPS="30"\n')
        with patch("app.routes.camera.CAM_ENV_PATH", env_file):
            resp = await client.post("/config", json={"width": 640, "height": 480, "fps": 30})
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 640
        assert body["fps"] == 30


class TestGetSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_exists(self, client, tmp_path):
        snap = tmp_path / "snapshot.jpg"
        snap.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG header
        with patch("app.routes.camera.get_settings") as mock_s:
            mock_s.return_value = MagicMock(snapshot_path=str(snap))
            resp = await client.get("/snapshot.jpg")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_snapshot_missing(self, client):
        with patch("app.routes.camera.get_settings") as mock_s:
            mock_s.return_value = MagicMock(snapshot_path="/nonexistent/snap.jpg")
            resp = await client.get("/snapshot.jpg")
        assert resp.status_code == 503  # service unavailable when no snapshot
