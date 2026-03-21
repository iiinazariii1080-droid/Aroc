"""Tests for grasp analysis integration in autotake (robot_scripts.py).

Covers: config threshold enforcement, algorithm param forwarding,
enabled/disabled flow, seal score validation, collision abort,
RPY clamping, single-cup checks, and exception fall-back.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch


def _base_config(**overrides):
    """Minimal autotake config with baseMove active + grasp analysis enabled."""
    cfg = {
        "prefix": {"active": False},
        "gripper": {"active": False},
        "baseMove": {"active": True, "posX": 5, "posY": 3, "posZ": -10},
        "postfix": {"active": False},
        "return": {"active": False},
        "graspAnalysis": {"enabled": True},
    }
    cfg["graspAnalysis"].update(overrides)
    return cfg


def _ga_response(**overrides):
    """Successful grasp analysis response dict."""
    resp = {
        "status": "OK",
        "roll_correction_deg": 2.0,
        "pitch_correction_deg": 1.5,
        "yaw_correction_deg": 5.0,
        "xy_correction_mm": [1.0, -0.5],
        "confidence": 0.85,
        "approach_clear": True,
        "reason_codes": [],
        "dual_cup_result": {
            "combined_seal": 0.90,
            "activation_mode": "BOTH",
            "cup_a": {"seal_score": 0.92},
            "cup_b": {"seal_score": 0.88},
        },
    }
    resp.update(overrides)
    return resp


@pytest.fixture
def mocks():
    patches = {
        "lift": patch("app.robot_scripts.lift"),
        "manipulator": patch("app.robot_scripts.manipulator"),
        "agv": patch("app.robot_scripts.agv"),
        "depth_camera": patch("app.robot_scripts.depth_camera"),
        "robot_lock": patch("app.robot_scripts.robot_lock", new_callable=lambda: asyncio.Lock),
    }
    started = {k: p.start() for k, p in patches.items()}
    for name in ("lift", "manipulator", "agv", "depth_camera"):
        for method in ("enable_motion", "fault_reset", "reference",
                       "depth", "change_tool_position", "complex_move_with_joints",
                       "gripper_take", "gripper_status", "go_to_pose", "status",
                       "position", "move", "current_joints_position",
                       "capture_depth_frame", "analyze_grasp"):
            setattr(started[name], method, AsyncMock())
    yield started
    for p in patches.values():
        p.stop()


def _setup(mocks, config, ga_resp=None, distance_mm=200.0):
    """Wire common mocks for a grasp-analysis autotake run.

    Default distance 200mm (< STAGE2_THRESHOLD=250mm) to skip stage-1
    Z-only move and go straight to stage-2 where grasp analysis runs.
    """
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": distance_mm / 1000.0})
    mocks["manipulator"].capture_depth_frame = AsyncMock(return_value={"frame_id": "f1"})
    if ga_resp is not None:
        mocks["manipulator"].analyze_grasp = AsyncMock(return_value=ga_resp)
    return (
        patch("app.robot_scripts.init_trajectory_table"),
        patch("app.robot_scripts.get_trajectory", return_value=config),
    )


# ── Grasp disabled: corrections stay zero ────────────────────────


@pytest.mark.asyncio
async def test_grasp_disabled_skips_analysis(mocks):
    """When graspAnalysis.enabled=False, analyze_grasp is never called."""
    config = _base_config()
    config["graspAnalysis"]["enabled"] = False
    p1, p2 = _setup(mocks, config, _ga_response())

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True
    mocks["manipulator"].analyze_grasp.assert_not_awaited()


@pytest.mark.asyncio
async def test_grasp_enabled_calls_analysis(mocks):
    """When graspAnalysis.enabled=True, analyze_grasp is called."""
    config = _base_config()
    p1, p2 = _setup(mocks, config, _ga_response())

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True
    mocks["manipulator"].analyze_grasp.assert_awaited_once()


# ── Algorithm params forwarding ──────────────────────────────────


@pytest.mark.asyncio
async def test_algorithm_params_forwarded(mocks):
    """Algorithm params from config are forwarded to analyze_grasp."""
    config = _base_config(
        yawSearchStepDeg=15.0,
        collisionEnvelopeMm=50.0,
        sealPerimeterPoints=24,
        cupGridStepMm=4.0,
    )
    p1, p2 = _setup(mocks, config, _ga_response())

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call_kwargs = mocks["manipulator"].analyze_grasp.call_args
    assert call_kwargs.kwargs["yaw_search_step_deg"] == 15.0
    assert call_kwargs.kwargs["collision_envelope_mm"] == 50.0
    assert call_kwargs.kwargs["seal_perimeter_points"] == 24
    assert call_kwargs.kwargs["cup_grid_step_mm"] == 4.0


@pytest.mark.asyncio
async def test_no_algo_params_when_absent(mocks):
    """When algo params not in config, they're not passed to analyze_grasp."""
    config = _base_config()  # no algo overrides
    p1, p2 = _setup(mocks, config, _ga_response())

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call_kwargs = mocks["manipulator"].analyze_grasp.call_args
    assert "yaw_search_step_deg" not in call_kwargs.kwargs
    assert "collision_envelope_mm" not in call_kwargs.kwargs


# ── RPY clamping ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rpy_clamping_applies(mocks):
    """Corrections beyond maxRollDeg / maxPitchDeg / maxYawDeg are clamped."""
    config = _base_config(maxRollDeg=3.0, maxPitchDeg=2.0, maxYawDeg=10.0)
    ga = _ga_response(
        roll_correction_deg=7.0,
        pitch_correction_deg=-5.0,
        yaw_correction_deg=25.0,
    )
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    # The move call should contain clamped RPY
    call = mocks["manipulator"].change_tool_position.call_args_list
    # Find the stage-2 move (first call when prefix is inactive)
    stage2_call = call[0]
    params = stage2_call[0][0]
    assert params.roll_offset_deg == 3.0
    assert params.pitch_offset_deg == -2.0
    assert params.yaw_offset_deg == 10.0


# ── Seal score enforcement ───────────────────────────────────────


@pytest.mark.asyncio
async def test_seal_below_threshold_zeroes_corrections(mocks):
    """When combined_seal < minSealScore, all corrections are zeroed."""
    config = _base_config(minSealScore=0.80)
    ga = _ga_response()
    ga["dual_cup_result"]["combined_seal"] = 0.60  # below threshold
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    # Corrections zeroed — move uses only base offsets
    assert not hasattr(params, "roll_offset_deg") or params.roll_offset_deg is None
    # XY should be just base offsets (5, 3), no grasp xy_corr
    assert params.x_offset_mm == 5
    assert params.y_offset_mm == 3


@pytest.mark.asyncio
async def test_seal_above_threshold_applies_corrections(mocks):
    """When combined_seal ≥ minSealScore, corrections are applied."""
    config = _base_config(minSealScore=0.70)
    ga = _ga_response()  # combined_seal = 0.90 by default
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    assert params.roll_offset_deg == 2.0  # from ga_response
    # XY = base (5, 3) + ga correction (1.0, -0.5) = (6.0, 2.5)
    assert params.x_offset_mm == 6.0
    assert params.y_offset_mm == 2.5


# ── Single-cup pick logic ────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_cup_rejected_when_disallowed(mocks):
    """When allowSingleCupPick=False and mode is CUP_A_ONLY, corrections are zeroed."""
    config = _base_config(allowSingleCupPick=False)
    ga = _ga_response()
    ga["dual_cup_result"]["activation_mode"] = "CUP_A_ONLY"
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    # Corrections zeroed — single cup mode rejected
    assert params.x_offset_mm == 5  # base offset only


@pytest.mark.asyncio
async def test_single_cup_allowed_with_good_seal(mocks):
    """Single cup mode accepted when allowSingleCupPick=True and seal passes."""
    config = _base_config(allowSingleCupPick=True, minSingleCupSeal=0.40)
    ga = _ga_response()
    ga["dual_cup_result"]["activation_mode"] = "CUP_A_ONLY"
    ga["dual_cup_result"]["cup_a"]["seal_score"] = 0.80
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    # Corrections applied
    assert params.roll_offset_deg == 2.0
    assert params.x_offset_mm == 6.0


@pytest.mark.asyncio
async def test_single_cup_rejected_by_cup_seal(mocks):
    """Single cup mode rejected when active cup seal < minSingleCupSeal."""
    config = _base_config(allowSingleCupPick=True, minSingleCupSeal=0.60)
    ga = _ga_response()
    ga["dual_cup_result"]["activation_mode"] = "CUP_B_ONLY"
    ga["dual_cup_result"]["cup_b"]["seal_score"] = 0.40  # below 0.60
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        await autotake(40)

    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    # Corrections zeroed
    assert params.x_offset_mm == 5  # base offset only


# ── Collision abort ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collision_abort_returns_false(mocks):
    """abortOnCollision=True + approach_clear=False → autotake returns False."""
    config = _base_config(abortOnCollision=True)
    ga = _ga_response(approach_clear=False)
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is False


@pytest.mark.asyncio
async def test_collision_no_abort_by_default(mocks):
    """abortOnCollision defaults to False — blocked corridor doesn't abort."""
    config = _base_config()  # abortOnCollision absent → default False
    ga = _ga_response(approach_clear=False)
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True


# ── FAILED analysis → fallback ───────────────────────────────────


@pytest.mark.asyncio
async def test_failed_analysis_uses_base_offsets(mocks):
    """When grasp analysis returns FAILED, corrections stay zero (base offsets only)."""
    config = _base_config()
    ga = {"status": "FAILED", "reason_codes": ["SEGMENTATION_FAILED"]}
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True
    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    assert params.x_offset_mm == 5  # base only
    assert params.y_offset_mm == 3


# ── Exception fall-back ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_analysis_exception_falls_back(mocks):
    """If analyze_grasp raises, autotake continues with base offsets."""
    config = _base_config()
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.2})
    mocks["manipulator"].capture_depth_frame = AsyncMock(return_value={"frame_id": "f1"})
    mocks["manipulator"].analyze_grasp = AsyncMock(side_effect=RuntimeError("service down"))

    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config):
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True


# ── DEGRADED status still applies corrections ────────────────────


@pytest.mark.asyncio
async def test_degraded_status_applies_corrections(mocks):
    """DEGRADED status applies corrections (with warning logged)."""
    config = _base_config()
    ga = _ga_response(status="DEGRADED", reason_codes=["LOW_EIGENVALUE_RATIO"])
    p1, p2 = _setup(mocks, config, ga)

    with p1, p2:
        from app.robot_scripts import autotake
        result = await autotake(40)

    assert result is True
    call = mocks["manipulator"].change_tool_position.call_args_list[0]
    params = call[0][0]
    assert params.roll_offset_deg == 2.0  # corrections applied despite DEGRADED
