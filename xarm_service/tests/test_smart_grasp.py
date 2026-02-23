"""Tests for the smart grasp pipeline: GraspVerifier, DepthService helpers, GripperController parsing."""
import sys, os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import importlib.util
import numpy as np
import pytest

from models.grasp_types import (
    GripperFeedback,
    GripperStatus,
    GraspOutcome,
    ObjectDetection,
    PixelCoord,
    VerificationResult,
    CameraIntrinsics,
)


def _load_module(name: str, path: str):
    """Load a single .py file as a module without triggering __init__.py chains."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load grasp_verify and picobot_lib directly
_gv = _load_module("grasp_verify", "drivers/xarm_driver/usecases/grasp_verify.py")
GraspVerifier = _gv.GraspVerifier

_pb = _load_module("picobot_lib", "drivers/xarm_driver/picobot_lib.py")
ModbusResponse = _pb.ModbusResponse
_EXPECTED_OK = _pb._EXPECTED_OK


def _parse_response(raw: list):
    """Replicates GripperController._parse_response for testing without xarm SDK."""
    resp = ModbusResponse(raw=list(raw))
    if raw and len(raw) >= 2:
        resp.slave_addr = raw[0]
        resp.function_code = raw[1]
        resp.data = raw[2:]
    resp.success = raw == _EXPECTED_OK or (len(raw) >= 2 and (raw[1] & 0x80) == 0)
    return resp


# ── ModbusResponse parsing ─────────────────────────────────────────────────

class TestModbusResponse:
    def test_parse_ok_response(self):
        raw = [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]
        resp = _parse_response(raw)
        assert resp.success is True
        assert resp.slave_addr == 1
        assert resp.function_code == 8

    def test_parse_error_response(self):
        # Function code with error bit set (0x87 = 0x07 | 0x80)
        raw = [1, 0x87, 0x02]
        resp = _parse_response(raw)
        assert resp.success is False
        assert resp.function_code == 0x87

    def test_parse_empty_response(self):
        resp = _parse_response([])
        assert resp.success is False
        assert resp.data == []

    def test_parse_normal_fc_no_error_bit(self):
        raw = [1, 7, 0, 1, 0, 0, 41, 1, 0, 0, 100]
        resp = _parse_response(raw)
        # function_code 7 has no error bit → success
        assert resp.success is True


# ── GraspVerifier ──────────────────────────────────────────────────────────

class TestGraspVerifier:
    def setup_method(self):
        self.verifier = GraspVerifier(torque_threshold=0.3)

    def test_vacuum_check_positive(self):
        assert self.verifier.check_vacuum(1) is True

    def test_vacuum_check_negative(self):
        assert self.verifier.check_vacuum(0) is False
        assert self.verifier.check_vacuum(-1) is False
        assert self.verifier.check_vacuum(-99) is False

    def test_torque_delta_detects_weight(self):
        baseline = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        current  = [0.0, 0.0, 0.0, 1.0, 1.0, 1.5]  # delta 0.5 on J6
        ok, deltas = self.verifier.check_torque_delta(baseline, current)
        assert ok is True
        assert deltas[5] == pytest.approx(0.5)

    def test_torque_delta_no_change(self):
        baseline = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        current  = [0.0, 0.0, 0.0, 1.0, 1.0, 1.1]  # delta 0.1 < threshold
        ok, _ = self.verifier.check_torque_delta(baseline, current)
        assert ok is False

    def test_torque_delta_short_list(self):
        ok, deltas = self.verifier.check_torque_delta([1.0], [1.0])
        assert ok is False
        assert deltas == []

    def test_depth_vanished(self):
        h, w = 480, 640
        before = np.full((h, w), 500, dtype=np.uint16)
        after = np.full((h, w), 500, dtype=np.uint16)
        # Object was at (100, 100, 50, 50) with depth 300
        before[100:150, 100:150] = 300
        # After lift, that region reverts to 500 (floor depth)
        det = ObjectDetection(
            center_px=PixelCoord(125, 125),
            bbox=(100, 100, 50, 50),
            depth_mm=300.0,
            size_mm=(30.0, 30.0),
            contour_area_px=2500,
        )
        result = self.verifier.check_depth_vanished(before, after, det)
        assert result is True

    def test_depth_still_present(self):
        h, w = 480, 640
        before = np.full((h, w), 500, dtype=np.uint16)
        after = np.full((h, w), 500, dtype=np.uint16)
        before[100:150, 100:150] = 300
        after[100:150, 100:150] = 300  # Object still there
        det = ObjectDetection(
            center_px=PixelCoord(125, 125),
            bbox=(100, 100, 50, 50),
            depth_mm=300.0,
            size_mm=(30.0, 30.0),
            contour_area_px=2500,
        )
        result = self.verifier.check_depth_vanished(before, after, det)
        assert result is False

    def test_combined_verify_majority(self):
        """2 of 3 channels pass → object_held = True."""
        baseline = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        current  = [0.0, 0.0, 0.0, 1.0, 1.0, 1.5]
        result = self.verifier.verify(
            vacuum_sdk_state=1,        # OK
            torque_baseline=baseline,
            torque_current=current,    # OK (delta 0.5)
            depth_before=None,
            depth_after=None,
            detection=None,            # depth check skipped → FAIL
        )
        assert result.object_held is True
        assert result.vacuum_ok is True
        assert result.torque_delta_ok is True
        assert result.depth_vanished is False

    def test_combined_verify_minority(self):
        """Only 1 of 3 channels pass → object_held = False."""
        baseline = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        current  = [0.0, 0.0, 0.0, 1.0, 1.0, 1.1]  # FAIL
        result = self.verifier.verify(
            vacuum_sdk_state=0,        # FAIL
            torque_baseline=baseline,
            torque_current=current,
        )
        assert result.object_held is False


# ── DepthService helpers ───────────────────────────────────────────────────

cv2 = pytest.importorskip("cv2", reason="opencv not installed")


class TestDepthServiceHelpers:
    def test_calibrate_distance(self):
        from services.depth_service import DepthService
        raw = 100.0
        calibrated = DepthService.calibrate_distance(raw)
        expected = 0.957 * 100.0 + 45.2
        assert calibrated == pytest.approx(expected, rel=1e-3)

    def test_robust_mean_filters_outliers(self):
        from services.depth_service import DepthService
        vals = [100.0, 102.0, 101.0, 150.0, 50.0]  # 50 and 150 are outliers
        mean = DepthService._robust_mean(vals)
        # After removing min(50) and max(150), mean of [100, 101, 102] = 101
        assert mean == pytest.approx(101.0, rel=1e-3)

    def test_robust_mean_small_list(self):
        from services.depth_service import DepthService
        vals = [100.0, 102.0]
        mean = DepthService._robust_mean(vals)
        assert mean == pytest.approx(101.0, rel=1e-3)

    def test_detect_objects_on_flat_surface(self):
        """A frame with a raised object (closer) should detect it."""
        from services.depth_service import DepthService
        ds = DepthService.__new__(DepthService)
        from models.grasp_types import CameraIntrinsics
        ds._intrinsics = CameraIntrinsics()

        frame = np.full((480, 640), 500, dtype=np.uint16)
        # Place an object at center: depth 400 (closer)
        frame[200:280, 280:360] = 400
        detections = ds.detect_objects(frame)
        assert len(detections) >= 1
        d = detections[0]
        assert d.depth_mm < 500
        assert d.size_mm[0] > 0
        assert d.size_mm[1] > 0

    def test_detect_objects_empty_frame(self):
        from services.depth_service import DepthService
        from models.grasp_types import CameraIntrinsics
        ds = DepthService.__new__(DepthService)
        ds._intrinsics = CameraIntrinsics()
        frame = np.zeros((480, 640), dtype=np.uint16)
        detections = ds.detect_objects(frame)
        assert detections == []

    def test_compute_grasp_point_center(self):
        from services.depth_service import DepthService
        from models.grasp_types import CameraIntrinsics
        ds = DepthService.__new__(DepthService)
        ds._intrinsics = CameraIntrinsics()
        det = ObjectDetection(
            center_px=PixelCoord(320, 240),  # exactly at optical center
            bbox=(300, 220, 40, 40),
            depth_mm=400.0,
            size_mm=(42.0, 42.0),
            contour_area_px=1600,
        )
        gp = ds.compute_grasp_point(det)
        # At optical center, offsets should be ~0
        assert abs(gp.position_mm.x) < 1.0
        assert abs(gp.position_mm.y) < 1.0
        assert gp.approach_depth_mm == 400.0
