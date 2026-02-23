"""Tests for app/config.py — config values, defaults, env overrides."""

import os
import sys
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_default_xarm_ip():
    from app.config import XARM_IP
    # Should be a valid IP (either from env or default)
    assert isinstance(XARM_IP, str)
    assert len(XARM_IP.split(".")) == 4


def test_workspace_dimensions():
    from app.config import WS_SIZE_MM, BASE_IN_WS_MM, WS_MARGIN_MM
    assert len(WS_SIZE_MM) == 3
    assert all(v > 0 for v in WS_SIZE_MM)
    assert len(BASE_IN_WS_MM) == 3
    assert WS_MARGIN_MM > 0


def test_motion_limits_positive():
    from app.config import TCP_SPEED_MM_S, TCP_ACC_MM_S2, JOINT_SPEED_DEG_S
    assert TCP_SPEED_MM_S > 0
    assert TCP_ACC_MM_S2 > 0
    assert JOINT_SPEED_DEG_S > 0


def test_camera_intrinsics():
    from app.config import CAMERA_FX, CAMERA_FY, CAMERA_CX, CAMERA_CY
    assert CAMERA_FX > 0
    assert CAMERA_FY > 0
    # Center should be roughly in frame
    assert 0 < CAMERA_CX < 1000
    assert 0 < CAMERA_CY < 1000


def test_grasp_config_ranges():
    from app.config import (
        GRASP_MAX_RETRIES,
        GRASP_LIFT_HEIGHT_MM,
        SMART_GRASP_TIMEOUT_S,
    )
    assert GRASP_MAX_RETRIES >= 1
    assert GRASP_LIFT_HEIGHT_MM > 0
    assert SMART_GRASP_TIMEOUT_S > 0


def test_gripper_watchdog_config():
    from app.config import GRIPPER_IDLE_TIMEOUT_S, GRIPPER_WATCHDOG_ENABLED
    assert isinstance(GRIPPER_IDLE_TIMEOUT_S, float)
    assert isinstance(GRIPPER_WATCHDOG_ENABLED, bool)


def test_depth_frame_size():
    from app.config import DEPTH_FRAME_WIDTH, DEPTH_FRAME_HEIGHT, DEPTH_FRAME_BYTES
    assert DEPTH_FRAME_WIDTH == 640
    assert DEPTH_FRAME_HEIGHT == 480
    assert DEPTH_FRAME_BYTES == 640 * 480 * 2
