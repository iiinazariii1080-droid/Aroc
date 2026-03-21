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
        "agv": patch("app.robot_scripts.agv"),
        "depth_camera": patch("app.robot_scripts.depth_camera"),
        "robot_lock": patch("app.robot_scripts.robot_lock", new_callable=lambda: asyncio.Lock),
    }
    started = {k: p.start() for k, p in patches.items()}
    # Make all methods async
    for name in ("lift", "manipulator", "agv", "depth_camera"):
        for method in ("enable_motion", "fault_reset", "reference",
                       "depth", "change_tool_position", "complex_move_with_joints",
                       "gripper_take", "gripper_status", "go_to_pose", "status",
                       "position", "move", "current_joints_position"):
            setattr(started[name], method, AsyncMock())
    # AGV defaults: stopped, online
    started["agv"].status = AsyncMock(return_value={"online": True, "velocity": {"vx_m_s": 0, "vy_m_s": 0}})
    started["agv"].pose = AsyncMock(return_value={"pose": {"x_m": 0, "y_m": 0, "theta_deg": 0}})
    yield started
    for p in patches.values():
        p.stop()


# ── autotake: depth camera fail path ────────────────────────────
@pytest.mark.asyncio
async def test_autotake_depth_camera_fail(mocks):
    """When depth camera fails, autotake returns False."""
    mocks["depth_camera"].depth = AsyncMock(side_effect=RuntimeError("cam offline"))
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={}):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is False


# ── autotake: distance too large ────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_distance_too_large(mocks):
    from exceptions import DeviceConnectionError
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 2.0})  # 2000 mm
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={}):
        from app.robot_scripts import autotake
        with pytest.raises(DeviceConnectionError, match="failed"):
            await autotake(40)


# ── autotake: distance too small ────────────────────────────────
@pytest.mark.asyncio
async def test_autotake_distance_too_small(mocks):
    from exceptions import DeviceConnectionError
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.02})  # 20 mm < MIN_DISTANCE_MM(30)
    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value={}):
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
         patch("app.robot_scripts.get_trajectory", return_value=config):
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
         patch("app.robot_scripts.get_trajectory", return_value=config):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True
    assert mocks["manipulator"].change_tool_position.await_count >= 2  # basemove stages + return


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
         patch("app.robot_scripts.get_trajectory", return_value=config):
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
         patch("app.robot_scripts.get_trajectory", return_value=config):
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
         patch("app.robot_scripts.get_trajectory", return_value=config):
        from app.robot_scripts import autotake
        result = await autotake(40)
    assert result is True


# ── autotake: CancelledError triggers _stop_all_devices ──────────
@pytest.mark.asyncio
async def test_autotake_cancelled_stops_devices(mocks):
    """When autotake is cancelled, _stop_all_devices is called and CancelledError re-raised."""
    # depth_camera returns valid depth to pass initial checks
    mocks["depth_camera"].depth = AsyncMock(return_value={"depth": 0.3})
    # change_tool_position raises CancelledError to simulate task cancellation
    mocks["manipulator"].change_tool_position = AsyncMock(side_effect=asyncio.CancelledError())

    config = {
        "prefix": {"active": True, "posX": 10, "posY": 20, "posZ": 30},
        "gripper": {"active": False},
        "baseMove": {"active": False, "posX": 0, "posY": 0, "posZ": 0},
        "postfix": {"active": False},
        "return": {"active": False},
    }

    with patch("app.robot_scripts.init_trajectory_table"), \
         patch("app.robot_scripts.get_trajectory", return_value=config), \
         patch("app.robot_scripts._stop_all_devices", new_callable=AsyncMock) as mock_stop:
        from app.robot_scripts import autotake
        with pytest.raises(asyncio.CancelledError):
            await autotake(40)
        mock_stop.assert_awaited_once()
