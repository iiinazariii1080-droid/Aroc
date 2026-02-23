"""Shared test fixtures and fakes.

Consolidates duplicated _FakeDrive / _FakeEventBus / _set_state helpers that
were independently maintained in three test files.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

import main
from app.events import EventType

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeDrive:
    """Minimal in-memory drive stub satisfying the contract used by routes / use-cases."""

    def __init__(self, *, is_connected: bool = True) -> None:
        self.is_connected = is_connected
        self.calls: list[tuple[str, dict | None]] = []

    # -- status / telemetry --------------------------------------------------

    async def get_status(self) -> dict[str, bool]:
        return {"fault": False, "operation_enabled": True, "remote": True}

    async def get_position(self) -> int:
        return 100

    async def is_motion(self) -> bool:
        return False

    async def is_homed(self) -> bool:
        return True

    async def read_u16(self, index: Any, sub: Any) -> int:
        return 0

    async def read_i32(self, index: Any, sub: Any) -> int:
        return 0

    async def read_i8(self, index: Any, sub: Any) -> int:
        return 1

    def telemetry_latest(self) -> None:
        return None

    # -- motion commands (record calls) --------------------------------------

    async def jog_stop(self, **kwargs: Any) -> None:
        self.calls.append(("jog_stop", kwargs or None))

    async def move_to_position(self, **kwargs: Any) -> None:
        self.calls.append(("move_to_position", kwargs))

    async def home(self, **kwargs: Any) -> str:
        self.calls.append(("home", kwargs))
        return "ok"

    async def fault_reset(self, **kwargs: Any) -> None:
        self.calls.append(("fault_reset", kwargs))

    async def quick_stop(self, **kwargs: Any) -> None:
        self.calls.append(("quick_stop", kwargs or None))

    async def stop(self, **kwargs: Any) -> None:
        self.calls.append(("stop", kwargs or None))


class AsyncNoopLock:
    """No-op async context-manager that satisfies ``async with motor_lock``."""

    async def __aenter__(self) -> AsyncNoopLock:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakeEventBus:
    """In-memory event bus that records publications for assertion."""

    def __init__(self) -> None:
        self.published: list[tuple[EventType, dict]] = []

    async def publish(self, event_type: EventType, payload: dict) -> None:
        self.published.append((event_type, payload))


# ---------------------------------------------------------------------------
# Default app-state settings
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "DRYVE_HOST": "127.0.0.1",
    "DRYVE_PORT": 502,
    "DRYVE_UNIT_ID": 1,
    "DRIVER_VERSION": "test",
    "DRYVE_TELEMETRY_POLL_S": 0.5,
    "DRYVE_HEALTH_WEIGHT_DISCONNECTED": 50,
    "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR": 30,
    "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE": 20,
    "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE": 30,
    "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX": 20,
}


def set_app_state(
    app: Any,
    *,
    drive: FakeDrive | None = None,
    event_bus: FakeEventBus | None = None,
    motor_lock: AsyncNoopLock | None = None,
    settings: dict[str, Any] | None = None,
) -> FakeDrive:
    """Populate ``app.state`` with sensible defaults for testing."""
    drv = drive or FakeDrive()
    app.state.drive = drv
    app.state.drive_last_error = None
    app.state.drive_last_telemetry_monotonic = time.monotonic()
    app.state.drive_fault_active = False
    app.state.drive_telemetry_callback_errors_total = 0
    app.state.settings = settings or dict(DEFAULT_SETTINGS)

    if event_bus is not None:
        app.state.event_bus = event_bus
    if motor_lock is not None:
        app.state.motor_lock = motor_lock

    return drv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def noop_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable startup/shutdown so TestClient doesn't hit real hardware."""
    async def _noop(_app: Any) -> None:
        return None

    monkeypatch.setattr(main, "startup", _noop)
    monkeypatch.setattr(main, "shutdown", _noop)
