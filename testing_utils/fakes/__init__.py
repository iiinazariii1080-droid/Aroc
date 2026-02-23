"""Fake xArm SDK objects for testing without real hardware.

Usage in conftest.py::

    from testing_utils.fakes.xarm import FakeXArm, patch_xarm_api

    @pytest.fixture
    def mock_xarm(monkeypatch):
        return patch_xarm_api(monkeypatch)
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock

import pytest


class FakeXArm:
    """In-memory stub for xarm.wrapper.XArmAPI.

    Records all motion calls and returns configurable state.
    """

    def __init__(self, *, connected: bool = True, error_code: int = 0) -> None:
        self.connected = connected
        self._error_code = error_code
        self._position = [100.0, 200.0, 300.0, 0.0, 0.0, 0.0]
        self._servo_angles = [0.0] * 7
        self._state = 2  # normal
        self._gripper_active = False
        self.calls: list[tuple[str, dict | None]] = []

    # ── status ──────────────────────────────────────────────────────────
    def get_robot_sn(self) -> str:
        return "FAKE-SN-001"

    def get_position(self) -> tuple[int, list[float]]:
        return (0, list(self._position))

    def get_servo_angle(self) -> tuple[int, list[float]]:
        return (0, list(self._servo_angles))

    def get_state(self) -> int:
        return self._state

    def get_err_warn_code(self) -> list[int]:
        return [self._error_code, 0]

    # ── motion ──────────────────────────────────────────────────────────
    def set_position(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(("set_position", {"args": args, **kwargs}))
        return 0

    def set_servo_angle(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(("set_servo_angle", {"args": args, **kwargs}))
        return 0

    def move_gohome(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(("move_gohome", kwargs or None))
        return 0

    def set_gripper_enable(self, enable: bool) -> int:
        self.calls.append(("set_gripper_enable", {"enable": enable}))
        return 0

    def set_suction_cup(self, on: bool, **kwargs: Any) -> int:
        self.calls.append(("set_suction_cup", {"on": on, **kwargs}))
        self._gripper_active = on
        return 0

    # ── safety / setup ──────────────────────────────────────────────────
    def motion_enable(self, enable: bool = True) -> int:
        self.calls.append(("motion_enable", {"enable": enable}))
        return 0

    def set_mode(self, mode: int) -> int:
        self.calls.append(("set_mode", {"mode": mode}))
        return 0

    def set_state(self, state: int) -> int:
        self.calls.append(("set_state", {"state": state}))
        self._state = state
        return 0

    def clean_error(self) -> int:
        self.calls.append(("clean_error", None))
        self._error_code = 0
        return 0

    def clean_warn(self) -> int:
        self.calls.append(("clean_warn", None))
        return 0

    def disconnect(self) -> None:
        self.connected = False

    # ── reduced mode / boundary ─────────────────────────────────────────
    def set_reduced_tcp_boundary(self, boundary: Any) -> int:
        return 0

    def set_reduced_mode(self, mode: Any) -> int:
        return 0

    # ── callbacks (no-ops) ──────────────────────────────────────────────
    def register_error_warn_changed_callback(self, cb: Any) -> None:
        pass

    def register_state_changed_callback(self, cb: Any) -> None:
        pass

    def register_count_changed_callback(self, cb: Any) -> None:
        pass

    def release_error_warn_changed_callback(self, cb: Any) -> None:
        pass

    def release_state_changed_callback(self, cb: Any) -> None:
        pass

    def release_count_changed_callback(self, cb: Any) -> None:
        pass


def patch_xarm_api(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeXArm:
    """Monkeypatch XArmAPI so it returns a FakeXArm instance.

    Works for the import path used in xarm_service::

        monkeypatch.setattr("drivers.xarm_driver.actor.actor.XArmAPI", ...)

    Returns the FakeXArm instance for assertions.
    """
    fake = FakeXArm(**kwargs)
    monkeypatch.setattr(
        "drivers.xarm_driver.actor.actor.XArmAPI",
        lambda *a, **k: fake,
    )
    return fake
