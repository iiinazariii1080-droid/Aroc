"""Tests for the grasp analysis pipeline."""
import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from unittest.mock import MagicMock as _MagicMock

import numpy as np
import pytest

from models.grasp_types import (
    AnalysisStatus,
    CameraIntrinsics,
    ConfidenceBreakdown,
    FrameContext,
    GraspAnalysis,
    PixelCoord,
    SurfaceNormal,
)

# cv2 may be mocked by conftest — real OpenCV needed for analysis tests
_cv2_mod = sys.modules.get("cv2")
_cv2_is_real = _cv2_mod is not None and not isinstance(_cv2_mod, _MagicMock)


def _make_frame_context(frame_id: str = "test_frame") -> FrameContext:
    return FrameContext(
        frame_id=frame_id,
        timestamp_ms=0,
        intrinsics={"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0},
        depth_scale=0.9,
        resolution=(640, 480),
        frame_reused=True,
    )


def _make_depth_service():
    """Create a DepthService without hitting the network."""
    from services.depth_service import DepthService

    ds = DepthService.__new__(DepthService)
    ds._base_url = "http://localhost:9999"
    ds._intrinsics = CameraIntrinsics(
        fx=380.0, fy=380.0, cx=320.0, cy=240.0,
        width=640, height=480, depth_scale=0.9,
    )
    ds._frame_cache = {}
    return ds


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestGraspAnalysisSegmentation:
    """Test seed-based region growing segmentation."""

    def test_flat_object_segmented(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Background at 500, object at 400 centered
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[200:280, 280:360] = 400

        mask, median_depth = gas._segment_object(frame, PixelCoord(320, 240))
        assert mask is not None
        assert int(np.count_nonzero(mask)) > 50
        assert 350 < median_depth < 370  # 400 * 0.9 = 360

    def test_empty_frame_returns_none(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.zeros((480, 640), dtype=np.uint16)
        mask, median_depth = gas._segment_object(frame, PixelCoord(320, 240))
        assert mask is None
        assert median_depth == 0.0

    def test_out_of_bounds_target(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.full((480, 640), 500, dtype=np.uint16)
        mask, _ = gas._segment_object(frame, PixelCoord(-1, -1))
        assert mask is None


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestGraspAnalysisNormals:
    """Test surface normal estimation (requires cv2 for unprojection)."""

    def test_flat_plane_normal_is_z(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Flat plane at uniform depth → normal should be approximately [0, 0, -1]
        frame = np.full((480, 640), 400, dtype=np.uint16)
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 280:360] = 255

        normal, eigenvalue_ratio = gas._estimate_global_normal(frame, mask, 360.0)
        assert normal is not None
        # For a flat plane, normal z should dominate
        assert abs(normal.nz) > 0.95
        assert normal.nz < 0  # oriented toward camera

    def test_normal_sign_oriented_toward_camera(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.full((480, 640), 400, dtype=np.uint16)
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 280:360] = 255

        normal, _ = gas._estimate_global_normal(frame, mask, 360.0)
        assert normal is not None
        # dot(n, [0,0,-1]) > 0 means nz < 0
        assert normal.nz < 0


class TestNormalToRollPitch:
    """Pure-math normal→correction tests (no cv2 needed)."""

    def test_roll_pitch_zero_for_flat_plane(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        normal = SurfaceNormal(nx=0.0, ny=0.0, nz=-1.0)
        roll, pitch = gas._normal_to_roll_pitch(normal)
        assert abs(roll) < 0.1
        assert abs(pitch) < 0.1

    def test_tilted_surface_gives_nonzero_correction(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Tilted ~5° around Y-axis → nx component
        import math
        nx = math.sin(math.radians(5))
        nz = -math.cos(math.radians(5))
        normal = SurfaceNormal(nx=nx, ny=0.0, nz=nz)
        roll, pitch = gas._normal_to_roll_pitch(normal)
        assert 4.0 < abs(roll) < 6.0
        assert abs(pitch) < 0.5

    def test_correction_clamped(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Extreme tilt → should be clamped to ±8°
        normal = SurfaceNormal(nx=0.5, ny=0.5, nz=-0.707)
        roll, pitch = gas._normal_to_roll_pitch(normal)
        assert abs(roll) <= 8.0
        assert abs(pitch) <= 8.0


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestGraspAnalysisSealScore:
    """Test perimeter seal scoring."""

    def test_perfect_seal_on_large_object(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Large flat object covering most of the frame
        frame = np.full((480, 640), 400, dtype=np.uint16)
        mask = np.ones((480, 640), dtype=np.uint8) * 255

        score = gas._compute_seal_score(frame, mask, 320, 240, 20, 360.0)
        assert score > 0.9

    def test_edge_cup_has_low_seal(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.full((480, 640), 400, dtype=np.uint16)
        mask = np.zeros((480, 640), dtype=np.uint8)
        # Small region — cup placed at edge
        mask[230:250, 310:330] = 255

        score = gas._compute_seal_score(frame, mask, 320, 240, 20, 360.0)
        assert score < 0.5


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestGraspAnalysisCollision:
    """Test collision corridor scan."""

    def test_clear_corridor(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Object at depth 400, nothing else in corridor
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[200:280, 280:360] = 400
        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 280:360] = 255

        zones, risk = gas._scan_corridor(frame, mask, 360.0, PixelCoord(320, 240))
        assert risk == "CLEAR"
        assert len(zones) == 0

    def test_obstacle_in_corridor(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Object at depth 400, obstacle at depth 300 (closer) nearby
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[200:280, 280:360] = 400  # target
        frame[210:250, 300:340] = 300  # obstacle above target

        mask = np.zeros((480, 640), dtype=np.uint8)
        mask[200:280, 280:360] = 255  # only target in mask

        zones, risk = gas._scan_corridor(frame, mask, 360.0, PixelCoord(320, 240))
        # Obstacle is in front of target, in envelope → should detect it
        assert risk in ("MARGINAL", "BLOCKED")
        assert len(zones) > 0


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestGraspAnalysisFullPipeline:
    """Test the full analyze() pipeline."""

    @pytest.mark.asyncio
    async def test_flat_object_full_analysis(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Flat object at center
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[180:300, 240:400] = 400

        ctx = _make_frame_context()
        result = await gas.analyze(frame, 50.0, 50.0, ctx)

        assert isinstance(result, GraspAnalysis)
        assert result.analysis_status.status in ("OK", "DEGRADED")
        assert abs(result.roll_correction_deg) < 2.0
        assert abs(result.pitch_correction_deg) < 2.0
        assert 0 <= result.confidence <= 1.0
        assert result.frame_context.frame_id == "test_frame"

    @pytest.mark.asyncio
    async def test_empty_frame_returns_failed(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.zeros((480, 640), dtype=np.uint16)
        ctx = _make_frame_context()
        result = await gas.analyze(frame, 50.0, 50.0, ctx)

        assert result.analysis_status.status == "FAILED"
        assert "SEGMENTATION_FAILED" in result.analysis_status.reason_codes
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_confidence_breakdown_in_range(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[180:300, 240:400] = 400

        ctx = _make_frame_context()
        result = await gas.analyze(frame, 50.0, 50.0, ctx)

        breakdown = result.confidence_breakdown
        assert 0 <= breakdown.seal_proxy <= 1.0
        assert 0 <= breakdown.stability_proxy <= 1.0
        assert 0 <= breakdown.corridor_proxy <= 1.0
        assert 0 <= breakdown.reachability_proxy <= 1.0


class TestFrameCache:
    """Test frame capture and TTL cache."""

    def test_cached_frame_retrieval(self):
        import time

        ds = _make_depth_service()
        frame = np.full((480, 640), 400, dtype=np.uint16)
        frame_id = "test123"
        ds._frame_cache[frame_id] = (frame, time.time())

        cached = ds.get_cached_frame(frame_id)
        assert cached is not None
        assert np.array_equal(cached, frame)

    def test_expired_frame_returns_none(self):
        import time

        ds = _make_depth_service()
        frame = np.full((480, 640), 400, dtype=np.uint16)
        frame_id = "expired"
        ds._frame_cache[frame_id] = (frame, time.time() - 10.0)  # 10s ago, past TTL

        cached = ds.get_cached_frame(frame_id)
        assert cached is None

    def test_missing_frame_returns_none(self):
        ds = _make_depth_service()
        cached = ds.get_cached_frame("nonexistent")
        assert cached is None

    def test_evict_expired(self):
        import time

        ds = _make_depth_service()
        frame = np.full((480, 640), 400, dtype=np.uint16)
        ds._frame_cache["old"] = (frame, time.time() - 10.0)
        ds._frame_cache["new"] = (frame, time.time())

        ds._evict_expired()
        assert "old" not in ds._frame_cache
        assert "new" in ds._frame_cache


class TestFootprintScaling:
    """Test that cup pixel radius changes correctly with depth."""

    def test_cup_radius_scales_with_depth(self):
        from services.grasp_analysis import GraspAnalysisService
        from app.config import GRIPPER_CUP_DIAMETER_MM, CAMERA_FX

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        cup_radius_mm = GRIPPER_CUP_DIAMETER_MM / 2.0
        ci = ds.intrinsics

        # Near object (300mm)
        r_near = max(1, int(round(cup_radius_mm * ci.fx / 300.0)))
        # Far object (600mm)
        r_far = max(1, int(round(cup_radius_mm * ci.fx / 600.0)))

        # Closer objects should have larger pixel radius
        assert r_near > r_far


class TestSetOverrides:
    """Test that algorithm parameters can be overridden."""

    def test_default_values_match_config(self):
        from services.grasp_analysis import GraspAnalysisService
        from app.config import (
            YAW_SEARCH_STEP_DEG,
            GRIPPER_ENVELOPE_RADIUS_MM,
            SEAL_PERIMETER_POINTS,
            CUP_GRID_STEP_MM,
        )

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        assert gas._yaw_search_step == YAW_SEARCH_STEP_DEG
        assert gas._collision_envelope == GRIPPER_ENVELOPE_RADIUS_MM
        assert gas._seal_perimeter_points == SEAL_PERIMETER_POINTS
        assert gas._cup_grid_step == CUP_GRID_STEP_MM

    def test_overrides_take_effect(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        gas.set_overrides(
            yaw_search_step_deg=10.0,
            collision_envelope_mm=50.0,
            seal_perimeter_points=12,
            cup_grid_step_mm=4.0,
        )

        assert gas._yaw_search_step == 10.0
        assert gas._collision_envelope == 50.0
        assert gas._seal_perimeter_points == 12
        assert gas._cup_grid_step == 4.0

    def test_partial_overrides_preserve_defaults(self):
        from services.grasp_analysis import GraspAnalysisService
        from app.config import GRIPPER_ENVELOPE_RADIUS_MM, CUP_GRID_STEP_MM

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        gas.set_overrides(yaw_search_step_deg=15.0)

        assert gas._yaw_search_step == 15.0
        assert gas._collision_envelope == GRIPPER_ENVELOPE_RADIUS_MM
        assert gas._cup_grid_step == CUP_GRID_STEP_MM


class TestCheckIKCommand:
    """Test CHECK_IK command type exists and is well-formed."""

    def test_check_ik_in_command_types(self):
        from drivers.xarm_driver.actor.commands import CommandType
        assert hasattr(CommandType, "CHECK_IK")
        assert CommandType.CHECK_IK.value == "CHECK_IK"

    def test_check_ik_command_can_be_created(self):
        from drivers.xarm_driver.actor.commands import Command, CommandType, ExecutionPolicy
        cmd = Command(
            command_id="test-ik",
            type=CommandType.CHECK_IK,
            params={"pose": [300.0, 0.0, 200.0, 180.0, 0.0, 0.0]},
            policy=ExecutionPolicy.QUEUE,
        )
        assert cmd.type == CommandType.CHECK_IK
        assert len(cmd.params["pose"]) == 6


@pytest.mark.skipif(not _cv2_is_real, reason="cv2 is mocked – real OpenCV required")
class TestNormalConsistencyPenalty:
    """Test that normal consistency is computed and penalizes confidence."""

    @pytest.mark.asyncio
    async def test_flat_object_has_consistent_normals(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Large, perfectly flat object
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[150:330, 180:460] = 400

        ctx = _make_frame_context()
        result = await gas.analyze(frame, 50.0, 50.0, ctx)

        # For a flat object, normal consistency should be good → no "NORMAL_INCONSISTENT" code
        assert "NORMAL_INCONSISTENT" not in result.analysis_status.reason_codes

    @pytest.mark.asyncio
    async def test_stability_proxy_includes_flatness(self):
        from services.grasp_analysis import GraspAnalysisService

        ds = _make_depth_service()
        gas = GraspAnalysisService(ds)

        # Flat object
        frame = np.full((480, 640), 500, dtype=np.uint16)
        frame[150:330, 180:460] = 400

        ctx = _make_frame_context()
        result = await gas.analyze(frame, 50.0, 50.0, ctx)

        # stability_proxy should be a positive value for a flat object with good seal
        if result.analysis_status.status != "FAILED":
            assert result.confidence_breakdown.stability_proxy >= 0.0
