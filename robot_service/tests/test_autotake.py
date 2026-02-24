"""Tests for autotake and move_robot_to_product start paths in robot_scripts.py.

These test the initial branches to gain line coverage on
the largest uncovered functions.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace


# build a common mocks fixture
@pytest.fixture
def mocks():
    patches = {
        "lift": patch("app.robot_scripts.lift"),
        "manipulator": patch("app.robot_scripts.manipulator"),
        "symovo": patch("app.robot_scripts.symovo"),
        "depth_camera": patch("app.robot_scripts.depth_camera"),
        "robot_lock": patch("app.robot_scripts.robot_lock", new_callable=lambda: asyncio.Lock),
    }
    started = {k: p.start() for k, p in patches.items()}
    # Make all methods async
    for name in ("lift", "manipulator", "symovo", "depth_camera"):
        for method in ("enable_motion", "fault_reset", "reference",
                       "depth", "change_tool_position", "complex_move_with_joints",
                       "gripper_take", "gripper_status", "go_to_pose", "status",
                       "position", "move", "current_joints_position"):
            setattr(started[name], method, AsyncMock())
    yield started
    for p in patches.values():
        p.stop()


# ── autotake: depth camera fail path ────────────────────────────
@pytest.mark.asyncio
async def test_autotake_depth_camera_fail(mocks):
    """When depth camera fails, autotake returns False."""
    mocks["depth_camera"].depth = AsyncMock(side_effect=RuntimeError("cam offline"))
    with patch("app.robot_scripts.init_trajectory_table"):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is False


# ── autotake: distance too large ────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_distance_too_large(mocks):
    from exceptions import DeviceConnectionError
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 2.0})  # 2000 mm
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={}), \
         patch("app.robot_scripts.calibrate_distance", return_value=2000.0):
        from app.robot_scripts import autotake
        with pytest.raises(DeviceConnectionError, match="failed"):
            await autotake(40)


# ── autotake: distance too small ────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_distance_too_small(mocks):
    from exceptions import DeviceConnectionError
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.05})  # 50 mm
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={}), \
         patch("app.robot_scripts.calibrate_distance", return_value=50.0):
        from app.robot_scripts import autotake
        with pytest.raises(DeviceConnectionError, match="failed"):
            await autotake(40)


# ── autotake: success path with prefix, no gripper, no basemove, no return ──
@pytest.mark.asyncio
async def test_autotake_happy_prefix_only(mocks):
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})  # 300mm
    config = {
        "prefix": {"active": True, "posX": 10, "posY": 20, "posZ": 30},
        "gripper": {"active": False},
        "baseMove": {"active": False, "posX": 0, "posY": 0, "posZ": 0},
        "postfix": {"active": False},
        "return": {"active": False},
    }
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts.calibrate_distance", return_value=300.0):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True
    mocks["manipulator"].change_tool_position.assert_awaited_once()


# ── autotake: with basemove and return active ────────────────────
@pytest.mark.asyncio
async def test_autotake_basemove_and_return(mocks):
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})
    config = {
        "prefix": {"active": False},
        "gripper": {"active": False},
        "baseMove": {"active": True, "posX": 5, "posY": 10, "posZ": 15},
        "postfix": {"active": False},
        "return": {"active": True},
    }
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts.calibrate_distance", return_value=300.0):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True
    assert mocks["manipulator"].change_tool_position.await_count == 2  # basemove + return


# ── autotake: with postfix ──────────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_postfix(mocks):
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})
    config = {
        "prefix": {"active": False},
        "gripper": {"active": False},
        "baseMove": {"active": False, "posX": 0, "posY": 0, "posZ": 0},
        "postfix": {"active": True, "posX": 1, "posY": 2, "posZ": 3},
        "return": {"active": False},
    }
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts.calibrate_distance", return_value=300.0):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True


# ── autotake: with gripper and simple verify ─────────────────────
@pytest.mark.asyncio
async def test_autotake_gripper_simple(mocks):
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})
    mocks["manipulator"].gripper_status = AsyncMock(return_value={"feedback": "PART_GRIPPED", "sensor_supported": True})
    config = {
        "prefix": {"active": False},
        "gripper": {"active": True},
        "gripperVerify": {"enabled": True, "samples": 1, "required": 1, "intervalMs": 50, "maxReadErrors": 1, "maxAutoRecoveries": 0},
        "baseMove": {"active": False, "posX": 0, "posY": 0, "posZ": 0},
        "postfix": {"active": False},
        "return": {"active": False},
    }
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts.calibrate_distance", return_value=300.0):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True


# ── autotake: depth compensation ─────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_depth_compensation(mocks):
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})
    config = {
        "depthCompensation": {"scale": 1.1, "bias": 5.0, "quad": 0.001},
        "prefix": {"active": False},
        "gripper": {"active": False},
        "baseMove": {"active": False, "posX": 0, "posY": 0, "posZ": 0},
        "postfix": {"active": False},
        "return": {"active": False},
    }
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts.calibrate_distance", return_value=300.0):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True
