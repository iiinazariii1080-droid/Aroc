"""Unit tests for app/robot_scripts.py orchestration functions.

Mock all external service clients (lift, manipulator, symovo, depth_camera)
at module level to avoid any hardware calls.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace


def _symovo_raw_ok(**overrides):
    """Raw dict that _normalize_symovo_status can convert to SymovoStatusResponse."""
    d = dict(
        online=True,
        enabled=True,
        pose={"x_m": 0, "y_m": 0, "theta_deg": 0, "map_id": 0},
        velocity={"vx_m_s": 0, "vy_m_s": 0, "omega_rad_s": 0},
    )
    d.update(overrides)
    return d


# ── helpers ────────────────────────────────────────────────────────
def _patch_services():
    """Return dict of patches for all module-level service clients."""
    return {
        "lift": patch("app.robot_scripts.lift"),
        "manipulator": patch("app.robot_scripts.manipulator"),
        "symovo": patch("app.robot_scripts.symovo"),
        "depth_camera": patch("app.robot_scripts.depth_camera"),
        "robot_lock": patch("app.robot_scripts.robot_lock", new_callable=lambda: asyncio.Lock),
    }


@pytest.fixture
def mocks():
    """Patch service singletons and the robot_lock."""
    patches = _patch_services()
    started = {k: p.start() for k, p in patches.items()}

    # Make all service methods async by default
    for name in ("lift", "manipulator", "symovo", "depth_camera"):
        for method in ("status", "fault_reset", "reference", "enable_motion",
                       "position", "move", "current_joints_position",
                       "complex_move_with_joints", "change_tool_position",
                       "gripper_take", "gripper_status", "go_to_pose", "pose",
                       "depth"):
            if not hasattr(started[name], method):
                setattr(started[name], method, AsyncMock())
            else:
                mock_attr = getattr(started[name], method)
                if not isinstance(mock_attr, AsyncMock):
                    setattr(started[name], method, AsyncMock())

    yield started

    for p in patches.values():
        p.stop()


# ── _ns_to_dict ────────────────────────────────────────────────────
def test_ns_to_dict_basic():
    from app.robot_scripts import _ns_to_dict
    ns = SimpleNamespace(a=1, b=SimpleNamespace(c=2))
    assert _ns_to_dict(ns) == {"a": 1, "b": {"c": 2}}


def test_ns_to_dict_pydantic():
    from app.robot_scripts import _ns_to_dict
    m = MagicMock()
    m.model_dump = MagicMock(return_value={"x": 10})
    del m.dict  # so model_dump is preferred
    assert _ns_to_dict(m) == {"x": 10}


def test_ns_to_dict_list():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict([1, 2]) == [1, 2]


def test_ns_to_dict_dict():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict({"a": 1}) == {"a": 1}


def test_ns_to_dict_scalar():
    from app.robot_scripts import _ns_to_dict
    assert _ns_to_dict(42) == 42


# ── fault_reset ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fault_reset_success(mocks):
    from app.robot_scripts import fault_reset
    result = await fault_reset()
    assert result is True
    mocks["lift"].fault_reset.assert_awaited_once()
    mocks["manipulator"].fault_reset.assert_awaited_once()
    mocks["symovo"].fault_reset.assert_awaited_once()
    mocks["manipulator"].enable_motion.assert_awaited_once()


@pytest.mark.asyncio
async def test_fault_reset_raises(mocks):
    mocks["manipulator"].fault_reset = AsyncMock(side_effect=RuntimeError("xarm down"))
    from app.robot_scripts import fault_reset
    with pytest.raises(RuntimeError, match="xarm down"):
        await fault_reset()


# ── igus_move_and_check ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_igus_move_and_check_success(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 1005})
    from app.robot_scripts import igus_move_and_check
    result = await igus_move_and_check(1000, 50)
    assert result is True


@pytest.mark.asyncio
async def test_igus_move_and_check_too_far(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 5000})
    from app.robot_scripts import igus_move_and_check
    result = await igus_move_and_check(1000, 50)
    assert result is None  # position too far


# ── set_ready ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_ready_success(mocks):
    from app import xarm_status
    # Set up xarm_status cache to show everything ready
    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["symovo"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import set_ready
    result = await set_ready()
    assert result is True


@pytest.mark.asyncio
async def test_set_ready_not_ready(mocks):
    from app import xarm_status
    from exceptions import DeviceReadyError
    # No cache → xarm not ready
    xarm_status._last_status = None
    xarm_status._last_updated_ts = 0.0
    mocks["lift"].status = AsyncMock(return_value={"connected": False})
    mocks["symovo"].status = AsyncMock(side_effect=RuntimeError("offline"))

    from app.robot_scripts import set_ready
    with pytest.raises(DeviceReadyError):
        await set_ready()


# ── move_robot_to_box_1 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_move_robot_to_box_1(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 30010})
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_robot_to_box_1
    result = await move_robot_to_box_1(40)
    assert result is True


@pytest.mark.asyncio
async def test_move_robot_to_box_2(mocks):
    mocks["lift"].position = AsyncMock(return_value={"position": 30010})
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_robot_to_box_2
    result = await move_robot_to_box_2(40)
    assert result is True


# ── move_to_transport_position ─────────────────────────────────────
@pytest.mark.asyncio
async def test_move_to_transport_position(mocks):
    mocks["lift"].position = AsyncMock(
        side_effect=[{"position": 20010}, {"position": 5}]
    )
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "SOME_POSE"}
    )
    mocks["manipulator"].complex_move_with_joints = AsyncMock()

    from app.robot_scripts import move_to_transport_position
    result = await move_to_transport_position(50)
    assert result is True


@pytest.mark.asyncio
async def test_move_to_transport_already_in_position(mocks):
    mocks["lift"].position = AsyncMock(
        side_effect=[{"position": 20010}, {"position": 5}]
    )
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )

    from app.robot_scripts import move_to_transport_position
    result = await move_to_transport_position(50)
    assert result is True


# ── get_robot_system_status ────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_robot_system_status_all_ready(mocks):
    import time
    from app import xarm_status
    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["symovo"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is True
    assert result["message"] == ""


@pytest.mark.asyncio
async def test_get_robot_system_status_igus_down(mocks):
    from app import xarm_status

    xarm_status.set_status({
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(side_effect=RuntimeError("igus offline"))
    mocks["symovo"].status = AsyncMock(return_value=_symovo_raw_ok())

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is False
    assert "Igus" in result["message"]


@pytest.mark.asyncio
async def test_get_robot_system_status_no_cache(mocks):
    from app import xarm_status
    xarm_status._last_status = None
    xarm_status._last_updated_ts = 0.0

    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["symovo"].status = AsyncMock(side_effect=RuntimeError("symovo offline"))

    from app.robot_scripts import get_robot_system_status
    result = await get_robot_system_status()
    assert result["ready"] is False


# ── _preflight_make_transport_safe ─────────────────────────────────
@pytest.mark.asyncio
async def test_preflight_all_ready(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_xarm_not_ready_recovers(mocks):
    # First status call: not ready. Second call after reset: ready.
    mocks["manipulator"].status = AsyncMock(side_effect=[
        {"connected": False},
        {"connected": True, "has_error": False, "has_err_warn": False, "motion_enabled": True},
    ])
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_igus_not_ready_recovers(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    # First not ready, second after reference: ready
    mocks["lift"].status = AsyncMock(side_effect=[
        {"connected": False},
        {"connected": True, "homed": True, "error": None},
    ])
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "TRANSPORT_STEP_2"}
    )
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_both_fail(mocks):
    from exceptions import DeviceReadyError
    mocks["manipulator"].status = AsyncMock(side_effect=RuntimeError("xarm fail"))
    mocks["manipulator"].fault_reset = AsyncMock(side_effect=RuntimeError("reset fail"))
    mocks["lift"].status = AsyncMock(side_effect=RuntimeError("igus fail"))
    mocks["lift"].fault_reset = AsyncMock(side_effect=RuntimeError("reset fail"))
    mocks["lift"].reference = AsyncMock(side_effect=RuntimeError("ref fail"))

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    with pytest.raises(DeviceReadyError, match="XArm.*Igus"):
        await _preflight_make_transport_safe(params)


@pytest.mark.asyncio
async def test_preflight_needs_manipulator_move(mocks):
    mocks["manipulator"].status = AsyncMock(return_value={
        "connected": True, "has_error": False, "has_err_warn": False
    })
    mocks["lift"].status = AsyncMock(return_value={
        "connected": True, "homed": True, "error": None
    })
    # Not at TRANSPORT_STEP_2 → must move
    mocks["manipulator"].current_joints_position = AsyncMock(
        return_value={"name": "SOME_OTHER_POSE"}
    )
    mocks["manipulator"].complex_move_with_joints = AsyncMock()
    mocks["lift"].position = AsyncMock(return_value={"position": 5})

    from app.robot_scripts import _preflight_make_transport_safe
    params = SimpleNamespace(velocity_percent=20)
    await _preflight_make_transport_safe(params)
    mocks["manipulator"].complex_move_with_joints.assert_awaited_once()
