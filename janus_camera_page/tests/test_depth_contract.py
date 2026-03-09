"""L2 Depth Semantic Contract tests.

Validates that CameraService frame data conforms to
DEPTH_SEMANTIC_CONTRACT.md (ratified 2026-03-04).

All tests use synthetic numpy data — no RealSense hardware required.

Markers: contract, unit
"""
from __future__ import annotations

import time
from unittest.mock import patch

import numpy as np
import pytest

# ── Contract constants (from DEPTH_SEMANTIC_CONTRACT.md) ──────────────

CONTRACT_DEPTH_WIDTH = 480    # post-rotation
CONTRACT_DEPTH_HEIGHT = 640   # post-rotation
CONTRACT_DEPTH_DTYPE = np.float32
CONTRACT_COLOR_DTYPE = np.uint8
CONTRACT_COLOR_CHANNELS = 3   # RGB24
CONTRACT_FPS = 15
CONTRACT_INTRINSICS = {
    "fx": 380.425,
    "fy": 380.425,
    "cx": 232.374,
    "cy": 324.825,
    "width": 480,
    "height": 640,
}

# Depth scale for D435 (Z16 units → meters)
CONTRACT_DEPTH_SCALE = 0.0010000000474974513  # ~0.001 m/unit


# ── Helpers ───────────────────────────────────────────────────────────

def _make_camera_service():
    """Create a CameraService instance with standard rotation settings."""
    # Import here to avoid issues if pyrealsense2 is not installed
    import importlib
    import sys

    # Mock pyrealsense2 so the import succeeds without hardware
    rs_mock = type(sys)("pyrealsense2")
    rs_mock.stream = type("stream", (), {
        "color": 0, "depth": 1, "infrared": 2,
    })()
    rs_mock.format = type("format", (), {
        "rgb8": 0, "z16": 1, "y8": 2,
    })()
    with patch.dict(sys.modules, {"pyrealsense2": rs_mock}):
        from realsense_mux import CameraService
        return CameraService(rotate="cw")


def _synthetic_z16(width: int = 640, height: int = 480, fill_mm: int = 1500) -> np.ndarray:
    """Create a synthetic Z16 depth frame (pre-rotation: HxW = 640x480 → rotated 480x640)."""
    return np.full((height, width), fill_mm, dtype=np.uint16)


def _synthetic_z16_with_zero_hole(width: int = 640, height: int = 480) -> np.ndarray:
    """Z16 frame with a zero hole (invalid depth)."""
    frame = np.full((height, width), 2000, dtype=np.uint16)
    frame[100:120, 200:220] = 0  # 20x20 hole
    return frame


def _synthetic_color_rgb(width: int = 640, height: int = 480) -> np.ndarray:
    """Create a synthetic RGB24 color frame (pre-rotation)."""
    return np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)


# ── Tests ─────────────────────────────────────────────────────────────

@pytest.mark.contract
class TestDepthFrameContract:
    """Verify depth frame shape, dtype, and unit after CameraService processing."""

    def test_depth_shape_post_rotation(self):
        """Contract: depth frame is 480×640 after 90° CW rotation."""
        svc = _make_camera_service()
        z16 = _synthetic_z16()
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        assert dm is not None
        assert dm["width"] == CONTRACT_DEPTH_WIDTH
        assert dm["height"] == CONTRACT_DEPTH_HEIGHT
        assert dm["array"].shape == (CONTRACT_DEPTH_HEIGHT, CONTRACT_DEPTH_WIDTH)

    def test_depth_dtype_float32(self):
        """Contract: depth values are float32."""
        svc = _make_camera_service()
        z16 = _synthetic_z16()
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        assert dm["array"].dtype == CONTRACT_DEPTH_DTYPE

    def test_depth_unit_meters(self):
        """Contract: depth values are in meters (Z16 * scale)."""
        svc = _make_camera_service()
        fill_mm = 1500
        z16 = _synthetic_z16(fill_mm=fill_mm)
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        expected_m = fill_mm * CONTRACT_DEPTH_SCALE
        # All non-zero pixels should be close to expected value
        np.testing.assert_allclose(
            dm["array"],
            expected_m,
            rtol=1e-5,
            err_msg=f"Depth values should be ~{expected_m:.4f} m",
        )

    def test_invalid_depth_z16_zero_maps_to_zero(self):
        """Contract: Z16=0 (invalid) → 0.0 meters."""
        svc = _make_camera_service()
        z16 = _synthetic_z16_with_zero_hole()
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        # After rotation, the zero region moves but zeros should remain zero
        zero_mask = dm["array"] == 0.0
        assert zero_mask.any(), "Z16=0 region must map to exactly 0.0 m"

    def test_depth_timestamp_present(self):
        """Contract: depth map includes a timestamp."""
        svc = _make_camera_service()
        z16 = _synthetic_z16()
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        assert "timestamp" in dm
        assert dm["timestamp"] > 0

    def test_depth_query_normalized_coords(self):
        """Contract: get_depth(x_norm, y_norm) with [0..1] coords returns valid float."""
        svc = _make_camera_service()
        z16 = _synthetic_z16(fill_mm=2000)
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        depth_m, i, j, W, H, ts = svc.get_depth(0.5, 0.5)
        assert isinstance(depth_m, float)
        assert depth_m > 0
        assert W == CONTRACT_DEPTH_WIDTH
        assert H == CONTRACT_DEPTH_HEIGHT

    def test_depth_query_boundary_coords(self):
        """Contract: boundary coords [0,0] and [1,1] are valid."""
        svc = _make_camera_service()
        z16 = _synthetic_z16(fill_mm=1000)
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        # Corner (0, 0)
        d0, _, _, _, _, _ = svc.get_depth(0.0, 0.0)
        assert isinstance(d0, float)
        # Corner (1, 1)
        d1, _, _, _, _, _ = svc.get_depth(1.0, 1.0)
        assert isinstance(d1, float)

    def test_depth_no_frame_raises(self):
        """Contract: get_depth before any frame → RuntimeError."""
        svc = _make_camera_service()
        with pytest.raises(RuntimeError, match="No depth frame"):
            svc.get_depth(0.5, 0.5)

    def test_depth_map_none_before_update(self):
        """Contract: get_depth_map before any frame → None."""
        svc = _make_camera_service()
        assert svc.get_depth_map() is None


@pytest.mark.contract
class TestColorFrameContract:
    """Verify color frame shape and dtype."""

    def test_color_shape_post_rotation(self):
        """Contract: after 90° CW rotation, color frame is (640, 480, 3)."""
        svc = _make_camera_service()
        # Pre-rotation color: (480, 640, 3) — note: rotation is done by caller
        # CameraService.update_color_rgb expects already-rotated frame
        rgb = np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)
        frame = svc.get_color_frame()
        assert frame is not None
        assert frame.shape == (640, 480, 3)

    def test_color_dtype_uint8(self):
        """Contract: color frame dtype is uint8 (RGB24)."""
        svc = _make_camera_service()
        rgb = np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)
        frame = svc.get_color_frame()
        assert frame.dtype == CONTRACT_COLOR_DTYPE

    def test_color_channels_rgb24(self):
        """Contract: color frame has 3 channels."""
        svc = _make_camera_service()
        rgb = np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)
        frame = svc.get_color_frame()
        assert frame.shape[2] == CONTRACT_COLOR_CHANNELS

    def test_color_frame_is_copy(self):
        """Contract: get_color_frame returns a copy, not a reference."""
        svc = _make_camera_service()
        rgb = np.zeros((640, 480, 3), dtype=np.uint8)
        svc.update_color_rgb(rgb)
        frame1 = svc.get_color_frame()
        frame1[0, 0, 0] = 255
        frame2 = svc.get_color_frame()
        assert frame2[0, 0, 0] == 0, "Mutation of returned frame must not affect internal state"

    def test_color_none_before_update(self):
        """Contract: get_color_frame before any update → None."""
        svc = _make_camera_service()
        assert svc.get_color_frame() is None


@pytest.mark.contract
class TestTimestampMonotonicity:
    """Verify that timestamps are monotonically increasing."""

    def test_depth_timestamps_increase(self):
        """Contract: successive depth updates produce increasing timestamps."""
        svc = _make_camera_service()
        timestamps = []
        for _ in range(20):
            z16 = _synthetic_z16(fill_mm=1000)
            svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
            dm = svc.get_depth_map()
            timestamps.append(dm["timestamp"])
            time.sleep(0.001)  # ensure time.time() advances
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1], (
                f"Timestamp #{i} ({timestamps[i]}) < #{i-1} ({timestamps[i-1]})"
            )

    def test_color_timestamps_increase(self):
        """Contract: successive color updates produce increasing timestamps."""
        svc = _make_camera_service()
        timestamps = []
        for _ in range(20):
            rgb = np.zeros((640, 480, 3), dtype=np.uint8)
            svc.update_color_rgb(rgb)
            timestamps.append(svc.get_color_timestamp())
            time.sleep(0.001)
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


@pytest.mark.contract
class TestDepthRotation:
    """Verify 90° CW rotation is applied correctly."""

    def test_rotation_maps_known_pixel(self):
        """A pixel at pre-rotation (row=0, col=0) should appear at a
        known post-rotation location after 90° CW (np.rot90(arr, 3))."""
        svc = _make_camera_service()
        z16 = np.zeros((480, 640), dtype=np.uint16)
        # Place a marker at pre-rotation [0, 0]
        z16[0, 0] = 5000
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        arr = dm["array"]
        # np.rot90(arr, 3) on (480,640) → (640,480)
        # rot90 k=3 (CW): new[j, H-1-i] = old[i, j] where H is old height
        # old[0,0] → new[0, 479]
        expected_val = 5000 * CONTRACT_DEPTH_SCALE
        assert abs(arr[0, 479] - expected_val) < 1e-4, (
            f"Rotated marker should be at [0, 479], got {arr[0, 479]}"
        )

    def test_rotation_output_shape(self):
        """Pre-rotation (480, 640) → post-rotation (640, 480)."""
        svc = _make_camera_service()
        z16 = np.zeros((480, 640), dtype=np.uint16)
        svc.update_depth_from_z16(z16, CONTRACT_DEPTH_SCALE)
        dm = svc.get_depth_map()
        # After rot90(k=3) on (480,640): shape becomes (640, 480)
        assert dm["array"].shape == (640, 480)


@pytest.mark.contract
class TestIntrinsics:
    """Verify camera intrinsics match the contract document."""

    def test_intrinsic_values(self):
        """Contract intrinsics are self-consistent."""
        fx = CONTRACT_INTRINSICS["fx"]
        fy = CONTRACT_INTRINSICS["fy"]
        cx = CONTRACT_INTRINSICS["cx"]
        cy = CONTRACT_INTRINSICS["cy"]
        w = CONTRACT_INTRINSICS["width"]
        h = CONTRACT_INTRINSICS["height"]
        # Basic sanity: principal point is within the image
        assert 0 < cx < w, f"cx={cx} outside image width={w}"
        assert 0 < cy < h, f"cy={cy} outside image height={h}"
        # Focal lengths are positive
        assert fx > 0
        assert fy > 0
        # Width and height match contract
        assert w == CONTRACT_DEPTH_WIDTH
        assert h == CONTRACT_DEPTH_HEIGHT
