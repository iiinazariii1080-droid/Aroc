"""Tests for robot_service config — defaults and validation."""

import os
import sys
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_server_ip_is_valid():
    from app.config import server_ip
    assert isinstance(server_ip, str)
    assert len(server_ip.split(".")) == 4


def test_service_urls_are_http():
    from app.config import (
        IGUS_CONTAINER_IP,
        IGUS_CONTAINER_PORT,
        XARM_CONTAINER_IP,
        XARM_CONTAINER_PORT,
    )
    # All ports should be numeric strings
    assert IGUS_CONTAINER_PORT.isdigit()
    assert XARM_CONTAINER_PORT.isdigit()


def test_joystick_defaults():
    from app.config import (
        JOYSTICK_DEADZONE,
        JOYSTICK_DEFAULT_TTL_MS,
        JOYSTICK_HOLD_TIMEOUT_MS,
    )
    assert 0 < JOYSTICK_DEADZONE < 1.0
    assert JOYSTICK_DEFAULT_TTL_MS > 0
    assert JOYSTICK_HOLD_TIMEOUT_MS > 0


def test_symovo_teleop_clamped():
    from app.config import (
        SYMOVO_TELEOP_DURATION,
        SYMOVO_TELEOP_LINEAR,
        SYMOVO_TELEOP_ANGULAR,
    )
    assert 0.05 <= SYMOVO_TELEOP_DURATION <= 2.0
    assert 0.01 <= SYMOVO_TELEOP_LINEAR <= 1.0
    assert 0.01 <= SYMOVO_TELEOP_ANGULAR <= 2.0
