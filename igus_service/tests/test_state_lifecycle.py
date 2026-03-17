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

    async def connect(self, *, telemetry_callback: Any = None) -> None:
        self.connected = True
        if telemetry_callback is not None:
            self._callback = telemetry_callback

    def set_telemetry_callback(self, cb: Any) -> None:
        self._callback = cb

    async def close(self) -> None:
        self.closed = True


def _fresh_app() -> FastAPI:
    """Return a bare FastAPI instance with no lifespan wiring."""
    return FastAPI()


def _make_snapshot(**overrides) -> MagicMock:
    """Return a MagicMock snapshot with sensible defaults."""
    snap = MagicMock()
    snap.cia402_state = overrides.get("cia402_state", "SWITCHED_ON")
    snap.decoded_status = overrides.get("decoded_status", {"fault": False})
    snap.statusword = overrides.get("statusword", 0x0237)
    snap.ts_monotonic_s = overrides.get("ts_monotonic_s", time.monotonic())
    snap.position = overrides.get("position", 100)
    snap.velocity = overrides.get("velocity", 0)
    snap.mode_display = overrides.get("mode_display", 1)
    return snap


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

    # Settings is a full Settings object
    from app.config import Settings
    settings = app.state.settings
    assert isinstance(settings, Settings)
    assert settings.dryve_host
    assert settings.dryve_port > 0


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
    # All attrs that startup() writes must be gone after shutdown.
    # This list mirrors _REQUIRED_STATE_ATTRS plus extra attrs from state.py.
    for attr in (
        "drive",
        "event_bus",
        "motor_lock",
        "settings",
        "drive_last_error",
        "drive_fault_active",
        "drive_last_telemetry_monotonic",
        "drive_telemetry_callback_errors_total",
        "latest_command_trace",
    ):
        assert not hasattr(app.state, attr), f"{attr!r} still present after shutdown"


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
    sub_queue = event_bus.subscribe()

    # First snapshot: establishes prev_state. STATE_CHANGE is only fired
    # on transitions (prev != current), so no event expected yet.
    snap1 = _make_snapshot(cia402_state="SWITCHED_ON", statusword=0x0237)
    callback(snap1)
    await asyncio.sleep(0.05)

    # Drain any STATUS/COMMAND events from the first snapshot
    while not sub_queue.empty():
        sub_queue.get_nowait()

    # Second snapshot with a different CiA402 state → STATE_CHANGE event
    snap2 = _make_snapshot(cia402_state="OPERATION_ENABLED", statusword=0x0637)
    callback(snap2)
    await asyncio.sleep(0.05)

    # Collect published events from the subscriber queue
    events = []
    while not sub_queue.empty():
        events.append(sub_queue.get_nowait())

    event_bus.unsubscribe(sub_queue)

    state_changes = [e for e in events if e.type == EventType.STATE_CHANGE]
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
    event_bus = app.state.event_bus
    sub_queue = event_bus.subscribe()

    # First snapshot: no fault — sets prev_state["fault"] = False
    snap1 = _make_snapshot(cia402_state="OPERATION_ENABLED", decoded_status={"fault": False}, statusword=0x0637)
    callback(snap1)
    await asyncio.sleep(0.05)

    # Drain initial STATUS events
    while not sub_queue.empty():
        sub_queue.get_nowait()

    # Second snapshot: fault appears → FAULT edge event
    snap2 = _make_snapshot(cia402_state="FAULT", decoded_status={"fault": True}, statusword=0x0008)
    callback(snap2)
    await asyncio.sleep(0.05)

    events = []
    while not sub_queue.empty():
        events.append(sub_queue.get_nowait())

    event_bus.unsubscribe(sub_queue)

    fault_events = [e for e in events if e.type == EventType.FAULT]
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

    # The increment is deferred via call_soon_threadsafe — give the event loop
    # a cycle to process it.
    await asyncio.sleep(0.01)

    assert app.state.drive_telemetry_callback_errors_total == 1


async def test_on_snapshot_continues_after_error() -> None:
    """After a callback error, subsequent valid snapshots must still be processed.

    Verifies the poller loop is not broken by a single bad snapshot.
    """
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    callback = fake_drive._callback

    # 1. Send a bad snapshot → error counter incremented
    callback(object())
    await asyncio.sleep(0.01)
    assert app.state.drive_telemetry_callback_errors_total == 1

    # 2. Send a valid snapshot → telemetry state should update normally
    good_snap = _make_snapshot(cia402_state="OPERATION_ENABLED")
    # drive_last_telemetry_monotonic is None until the first successful snapshot
    assert app.state.drive_last_telemetry_monotonic is None
    callback(good_snap)
    await asyncio.sleep(0.05)

    # Telemetry timestamp must now be set (proves _process_snapshot_in_loop ran)
    assert app.state.drive_last_telemetry_monotonic is not None
    # The good snapshot was processed successfully — no additional errors.
    # Note: counter may be 0 if the 60s decay window triggered during
    # _process_snapshot_in_loop (expected when _error_counter_reset_s starts at 0).
    assert app.state.drive_telemetry_callback_errors_total <= 1


async def test_on_snapshot_error_counter_decays() -> None:
    """The callback error counter resets to 0 after the 60-second decay window.

    The decay is triggered by _process_snapshot_in_loop when now_monotonic
    exceeds the last reset time by _ERROR_COUNTER_WINDOW_S (60s).
    """
    app = _fresh_app()
    fake_drive = FakeDryveD1()

    with patch("app.state.DryveD1", return_value=fake_drive):
        await state.startup(app)

    callback = fake_drive._callback

    # 1. Trigger a callback error → counter = 1
    callback(object())
    await asyncio.sleep(0.01)
    assert app.state.drive_telemetry_callback_errors_total == 1

    # 2. Send a normal snapshot with time.monotonic() advanced by 61 seconds.
    #    This causes _process_snapshot_in_loop to see the 60s window has elapsed
    #    and reset the counter to 0.
    base_time = time.monotonic()
    with patch("app.state.time") as mock_time:
        mock_time.monotonic.return_value = base_time + 61.0
        good_snap = _make_snapshot(cia402_state="OPERATION_ENABLED")
        callback(good_snap)

    await asyncio.sleep(0.05)

    # Counter should have been reset by the decay logic
    assert app.state.drive_telemetry_callback_errors_total == 0
