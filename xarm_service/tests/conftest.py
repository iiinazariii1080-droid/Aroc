"""Shared fixtures for xarm_service tests.

Patches hardware dependencies so tests run without real xArm or cameras.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure xarm_service root is on the path
_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)


# ── Fake lifespan (no hardware) ────────────────────────────────────────────

@pytest.fixture
def noop_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown so TestClient doesn't hit real hardware."""
    import app.state as state_mod
    import app.di as di_mod

    async def _noop_init():
        pass

    async def _noop_shutdown():
        pass

    monkeypatch.setattr(state_mod, "init_state", _noop_init)
    monkeypatch.setattr(state_mod, "shutdown_state", _noop_shutdown)
    monkeypatch.setattr(di_mod, "init_di", lambda: None)
    monkeypatch.setattr(di_mod, "shutdown_di", lambda: None)


# ── Fake xArm SDK ──────────────────────────────────────────────────────────

class FakeXArm:
    """In-memory stub for xarm.wrapper.XArmAPI."""

    connected = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._position = [100.0, 200.0, 300.0, 0.0, 0.0, 0.0]

    def get_robot_sn(self) -> str:
        return "FAKE-SN-001"

    def get_position(self) -> tuple[int, list[float]]:
        return (0, list(self._position))

    def get_servo_angle(self) -> tuple[int, list[float]]:
        return (0, [0.0] * 7)

    def get_state(self) -> int:
        return 2

    def get_err_warn_code(self) -> list[int]:
        return [0, 0]

    def set_position(self, *a: Any, **kw: Any) -> int:
        self.calls.append(("set_position", kw))
        return 0

    def set_servo_angle(self, *a: Any, **kw: Any) -> int:
        self.calls.append(("set_servo_angle", kw))
        return 0

    def motion_enable(self, enable: bool = True) -> int:
        return 0

    def set_mode(self, mode: int) -> int:
        return 0

    def set_state(self, state: int) -> int:
        return 0

    def clean_error(self) -> int:
        return 0

    def clean_warn(self) -> int:
        return 0

    def disconnect(self) -> None:
        self.connected = False

    def set_reduced_tcp_boundary(self, b: Any) -> int:
        return 0

    def set_reduced_mode(self, m: Any) -> int:
        return 0

    def set_suction_cup(self, on: bool, **kw: Any) -> int:
        self.calls.append(("set_suction_cup", {"on": on}))
        return 0

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


@pytest.fixture
def fake_xarm() -> FakeXArm:
    """Return a FakeXArm instance."""
    return FakeXArm()


@pytest.fixture
def mock_xarm_api(monkeypatch: pytest.MonkeyPatch) -> FakeXArm:
    """Patch XArmAPI globally so RobotActor uses a FakeXArm."""
    fake = FakeXArm()
    monkeypatch.setattr(
        "drivers.xarm_driver.actor.actor.XArmAPI",
        lambda *a, **k: fake,
    )
    return fake
