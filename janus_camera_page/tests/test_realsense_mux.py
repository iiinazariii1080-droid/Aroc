"""Tests for realsense_mux.py — pure logic (no hardware required).

Tests rotation, CameraService depth/color logic, and FastAPI routes
with mocked CameraService.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Mock pyrealsense2 before importing realsense_mux
_mock_rs = MagicMock()
_mock_rs.stream = MagicMock()
_mock_rs.format = MagicMock()
sys.modules.setdefault("pyrealsense2", _mock_rs)

from realsense_mux import rotate_img, CameraService  # noqa: E402


# ── rotate_img ──


class TestRotateImg:
    def _make_arr(self):
        return np.array([[1, 2], [3, 4]], dtype=np.uint8)

    def test_rotate_none(self):
        arr = self._make_arr()
        result = rotate_img(arr, "none")
        np.testing.assert_array_equal(result, arr)

    def test_rotate_cw(self):
        arr = self._make_arr()
        result = rotate_img(arr, "cw")
        expected = np.rot90(arr, 3)
        np.testing.assert_array_equal(result, expected)

    def test_rotate_ccw(self):
        arr = self._make_arr()
        result = rotate_img(arr, "ccw")
        expected = np.rot90(arr, 1)
        np.testing.assert_array_equal(result, expected)

    def test_rotate_flip(self):
        arr = self._make_arr()
        result = rotate_img(arr, "flip")
        expected = np.flipud(arr)
        np.testing.assert_array_equal(result, expected)

    def test_rotate_unknown_passthrough(self):
        arr = self._make_arr()
        result = rotate_img(arr, "unknown_mode")
        np.testing.assert_array_equal(result, arr)


# ── CameraService ──


class TestCameraService:
    def _make_service(self, **kwargs):
        return CameraService(**kwargs)

    def test_get_depth_no_frame_raises(self):
        svc = self._make_service()
        with pytest.raises(RuntimeError, match="No depth frame"):
            svc.get_depth(0.5, 0.5)

    def test_update_and_get_depth(self):
        svc = self._make_service()
        z16 = np.array([[100, 200], [300, 400]], dtype=np.uint16)
        scale = 0.001  # 1mm per unit
        svc.update_depth_from_z16(z16, scale)

        depth_val, i, j, W, H, ts = svc.get_depth(0.0, 0.0)
        assert W == 2
        assert H == 2
        assert ts > 0

    def test_get_depth_clamps_coordinates(self):
        svc = self._make_service()
        z16 = np.ones((4, 4), dtype=np.uint16) * 500
        svc.update_depth_from_z16(z16, 0.001)

        # Out-of-range coordinates clamped to [0, 1]
        depth_val, i, j, W, H, _ = svc.get_depth(-1.0, 2.0)
        assert j == 0  # x clamped to 0
        assert i == H - 1  # y clamped to 1.0

    def test_get_depth_with_flip_x(self):
        svc = self._make_service(flip_x=True)
        z16 = np.array([[100, 200], [300, 400]], dtype=np.uint16)
        svc.update_depth_from_z16(z16, 0.001)

        # x=0.0 with flip_x → becomes 1.0 → j=W-1=1
        _, _, j, _, _, _ = svc.get_depth(0.0, 0.0)
        assert j == 1

    def test_get_depth_with_flip_y(self):
        svc = self._make_service(flip_y=True)
        z16 = np.array([[100, 200], [300, 400]], dtype=np.uint16)
        svc.update_depth_from_z16(z16, 0.001)

        # y=0.0 with flip_y → becomes 1.0 → i=H-1=1
        _, i, _, _, _, _ = svc.get_depth(0.0, 0.0)
        assert i == 1

    def test_update_and_get_color_frame(self):
        svc = self._make_service()
        assert svc.get_color_frame() is None

        rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)

        result = svc.get_color_frame()
        assert result is not None
        assert result.shape == (480, 640, 3)

    def test_color_frame_is_copy(self):
        svc = self._make_service()
        rgb = np.zeros((2, 2, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)

        frame1 = svc.get_color_frame()
        frame2 = svc.get_color_frame()
        assert frame1 is not frame2  # must be independent copies

    def test_get_depth_map_none_initially(self):
        svc = self._make_service()
        assert svc.get_depth_map() is None

    def test_get_depth_map_returns_metadata(self):
        svc = self._make_service()
        z16 = np.ones((4, 6), dtype=np.uint16) * 1000
        svc.update_depth_from_z16(z16, 0.001)

        result = svc.get_depth_map()
        assert result is not None
        assert result["width"] == 6
        assert result["height"] == 4
        assert result["timestamp"] > 0
        assert result["array"].dtype == np.float32

    def test_depth_flip180(self):
        svc = self._make_service(depth_flip180=True)
        z16 = np.array([[1, 2], [3, 4]], dtype=np.uint16)
        svc.update_depth_from_z16(z16, 1.0)

        result = svc.get_depth_map()
        expected = np.array([[4, 3], [2, 1]], dtype=np.float32)
        np.testing.assert_array_equal(result["array"], expected)


# ── FastAPI routes (mocked service) ──


@pytest.fixture
def mux_app():
    """Create a FastAPI app with a mocked CameraService."""
    from realsense_mux import make_fastapi, CameraService
    svc = CameraService()
    # Pre-populate with test data
    z16 = np.ones((100, 100), dtype=np.uint16) * 500
    svc.update_depth_from_z16(z16, 0.001)
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    svc.update_color_rgb(rgb)
    return make_fastapi(svc)


@pytest.mark.asyncio
async def test_mux_depth_endpoint(mux_app):
    """GET /depth returns depth value."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=mux_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/depth", params={"x": 50, "y": 50})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "depth"
        assert "depth" in data


@pytest.mark.asyncio
async def test_mux_health_endpoint(mux_app):
    """GET /health returns status."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=mux_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert data["depth_available"] is True


@pytest.mark.asyncio
async def test_mux_depth_map_endpoint(mux_app):
    """GET /depth_map returns depth map data."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=mux_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/depth_map")
        assert resp.status_code == 200
        data = resp.json()
        assert "width" in data
        assert "height" in data


@pytest.mark.asyncio
async def test_mux_color_frame_endpoint(mux_app):
    """GET /color_frame returns color frame data."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=mux_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/color_frame")
        assert resp.status_code == 200
        data = resp.json()
        assert "width" in data
        assert "height" in data


class TestFifoTimeout:
    """DEF-08: FIFO timeout must not exceed watchdog stale threshold."""

    def test_default_fifo_timeout_within_watchdog_threshold(self):
        """FIFO timeout default must be ≤ 10s to prevent FDIR cascade (DEF-08)."""
        from realsense_mux import _FIFO_OPEN_TIMEOUT_SEC
        assert _FIFO_OPEN_TIMEOUT_SEC <= 10, (
            f"FIFO timeout {_FIFO_OPEN_TIMEOUT_SEC}s exceeds watchdog_stale_ms (10s), "
            "risking FDIR cascade (DEF-08)"
        )
