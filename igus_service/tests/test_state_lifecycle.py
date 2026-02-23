"""Tests for app/state.py — startup, shutdown, and telemetry callback.

Covers:
- startup initializes all expected app.state attributes
- startup survives DryveD1 connection failure (degraded mode)
- shutdown cleans up all state attributes
- on_snapshot callback publishes state-change and fault events
- on_snapshot callback errors are counted, not propagated
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app import state
from app.events import EventType

# ── Helpers ──────────────────────────────────────────────────────

class FakeDryveD1:
    """In-memory DryveD1 replacement for state.py tests."""

    def __init__(self, *, config: Any = None) -> None:
        self.config = config
        self.connected = False
        self._callback: Any = None
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    def set_telemetry_callback(self, cb: Any) -> None:
        self._callback = cb

    async def close(self) -> None:
        self.closed = True


def _fresh_app() -> FastAPI:
    """Return a bare FastAPI instance with no lifespan wiring."""
    return FastAPI()


# ── _cfg ────────────────────────────────────────────────────────

def test_cfg_returns_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as app_config
    monkeypatch.setattr(app_config, "DRYVE_HOST", "10.0.0.1")
    assert state._cfg("DRYVE_HOST", "fallback") == "10.0.0.1"


def test_cfg_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as app_config
    monkeypatch.delattr(app_config, "NO_SUCH_ATTR", raising=False)
    assert state._cfg("NO_SUCH_ATTR", 42) == 42


# ── startup (success path) ──────────────────────────────────────

async def test_startup_initializes_state() -> None:
    """Startup should populate every expected app.state slot."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    # Core attributes
    assert isinstance(app.state.motor_lock, asyncio.Lock)
    assert app.state.event_bus is not None
    assert app.state.latest_command_trace is None
    assert app.state.drive is fake_drive
    assert fake_drive.connected is True

    # Telemetry-related defaults
    assert app.state.drive_last_error is None
    assert app.state.drive_fault_active is False
    assert app.state.drive_telemetry_callback_errors_total == 0

    # Settings dict contains expected keys
    settings = app.state.settings
    assert "DRYVE_HOST" in settings
    assert "DRIVER_VERSION" in settings
    assert "DRYVE_PORT" in settings


# ── startup (connection failure) ─────────────────────────────────

async def test_startup_survives_connection_error() -> None:
    """When DryveD1 connect() raises, startup should NOT propagate.

    The app enters degraded mode: drive=None, drive_last_error set.
    """
    app = _fresh_app()
    failing_drive = FakeDryveD1()
    failing_drive.connect = AsyncMock(side_effect=ConnectionError("host unreachable"))

    with patch("app.state.DryveD1", return_value=failing_drive):
        await state.startup(app)

    assert app.state.drive is None
    assert app.state.drive_last_error is not None
    assert "unreachable" in app.state.drive_last_error


# ── shutdown ─────────────────────────────────────────────────────

async def test_shutdown_cleans_up_state() -> None:
    """After shutdown, all state attributes should be removed."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    await state.shutdown(app)

    assert fake_drive.closed is True
    # All known state attrs should be gone
    for attr in (
        "drive", "event_bus", "motor_lock", "settings",
        "drive_last_error", "drive_fault_active",
    ):
        assert not hasattr(app.state, attr), f"{attr} still present after shutdown"


async def test_shutdown_survives_close_error() -> None:
    """Shutdown should NOT propagate if drive.close() raises."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()
    fake_drive.close = AsyncMock(side_effect=OSError("socket gone"))

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    # Should not raise
    await state.shutdown(app)
    # Attributes still cleaned up
    assert not hasattr(app.state, "drive")


async def test_shutdown_noop_when_no_drive() -> None:
    """Shutdown should be safe even when startup was never called."""
    app = _fresh_app()
    # No startup — nothing on app.state
    await state.shutdown(app)
    # Just ensure it didn't crash


# ── on_snapshot callback ────────────────────────────────────────

async def test_on_snapshot_publishes_state_change() -> None:
    """The telemetry callback should publish STATE_CHANGE events."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    callback = fake_drive._callback
    assert callback is not None, "set_telemetry_callback was never called"

    event_bus = app.state.event_bus

    # Simulate first snapshot (prev_state is None → no event)
    snap1 = MagicMock()
    snap1.cia402_state = "SWITCHED_ON"
    snap1.decoded_status = {"fault": False}
    snap1.statusword = 0x0237
    snap1.ts_monotonic_s = time.monotonic()
    snap1.position = 100
    snap1.velocity = 0
    snap1.mode_display = 1
    callback(snap1)

    # Let the fire-and-forget tasks run
    await asyncio.sleep(0.05)

    # Second snapshot with different state → STATE_CHANGE event
    snap2 = MagicMock()
    snap2.cia402_state = "OPERATION_ENABLED"
    snap2.decoded_status = {"fault": False}
    snap2.statusword = 0x0637
    snap2.ts_monotonic_s = time.monotonic()
    snap2.position = 100
    snap2.velocity = 0
    snap2.mode_display = 1
    callback(snap2)

    await asyncio.sleep(0.05)

    recent = await event_bus.get_recent_events(limit=20)
    state_changes = [e for e in recent if e.type == EventType.STATE_CHANGE]
    assert len(state_changes) >= 1
    assert state_changes[-1].payload["from_state"] == "SWITCHED_ON"
    assert state_changes[-1].payload["to_state"] == "OPERATION_ENABLED"


async def test_on_snapshot_publishes_fault_event() -> None:
    """When fault goes from False → True, a FAULT event is published."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    callback = fake_drive._callback

    # First snapshot: no fault
    snap1 = MagicMock()
    snap1.cia402_state = "OPERATION_ENABLED"
    snap1.decoded_status = {"fault": False}
    snap1.statusword = 0x0637
    snap1.ts_monotonic_s = time.monotonic()
    snap1.position = 100
    snap1.velocity = 0
    snap1.mode_display = 1
    callback(snap1)
    await asyncio.sleep(0.05)

    # Second snapshot: fault appears
    snap2 = MagicMock()
    snap2.cia402_state = "FAULT"
    snap2.decoded_status = {"fault": True}
    snap2.statusword = 0x0008
    snap2.ts_monotonic_s = time.monotonic()
    snap2.position = 100
    snap2.velocity = 0
    snap2.mode_display = 1
    callback(snap2)
    await asyncio.sleep(0.05)

    event_bus = app.state.event_bus
    recent = await event_bus.get_recent_events(limit=20)
    fault_events = [e for e in recent if e.type == EventType.FAULT]
    assert len(fault_events) >= 1
    assert fault_events[-1].payload["active"] is True


async def test_on_snapshot_callback_error_counted() -> None:
    """If on_snapshot raises, the error counter is incremented (not propagated)."""
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    callback = fake_drive._callback

    # Pass a snapshot that will cause an AttributeError (missing fields)
    bad_snap = object()
    callback(bad_snap)

    assert app.state.drive_telemetry_callback_errors_total == 1
