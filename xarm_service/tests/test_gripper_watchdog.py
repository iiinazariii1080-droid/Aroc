"""Tests for GripperWatchdog – idle-vacuum auto-release timer."""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from drivers.xarm_driver.safety.gripper_watchdog import GripperWatchdog
from drivers.xarm_driver.state.models import RobotState
from drivers.xarm_driver.state.store import StateStore


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_store(gripper_active: bool = False, activated_at: Optional[float] = None) -> StateStore:
    """Return a real StateStore pre-seeded with gripper state."""
    store = StateStore()
    store._robot.gripper_active = gripper_active
    store._robot.gripper_activated_at = activated_at
    return store


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_after_idle_timeout():
    """Watchdog fires GRIP_OPEN when vacuum is idle > timeout and no part held."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(return_value=0)  # vacuum on, no part
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=5.0,
        rate_hz=50.0,  # fast loop for test
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_awaited()
    vacuum_reader.assert_called()


@pytest.mark.asyncio
async def test_no_release_when_part_gripped():
    """Watchdog extends the timer when vacuum sensor reports PART_GRIPPED (1)."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(return_value=1)  # part gripped
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=5.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_not_awaited()
    # Timer was refreshed: activated_at should now be recent
    assert store._robot.gripper_activated_at is not None
    assert time.time() - store._robot.gripper_activated_at < 2.0


@pytest.mark.asyncio
async def test_no_action_when_gripper_inactive():
    """Watchdog does nothing when gripper is OFF."""
    store = _make_store(gripper_active=False)
    vacuum_reader = MagicMock(return_value=-1)
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=1.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_not_awaited()
    vacuum_reader.assert_not_called()


@pytest.mark.asyncio
async def test_no_release_before_timeout():
    """Watchdog does not fire before the timeout elapses."""
    store = _make_store(gripper_active=True, activated_at=time.time())
    vacuum_reader = MagicMock(return_value=0)
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=999.0,  # way in the future
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_on_sensor_error():
    """When vacuum sensor returns -99 (error/degraded), treat as no-part → release."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(return_value=-99)  # sensor error
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=5.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_fires_only_once_per_cycle():
    """Watchdog should not spam GRIP_OPEN — fires once then waits for reset."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(return_value=0)
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=1.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.3)  # enough for multiple ticks
    await wd.stop()

    assert release_cb.await_count == 1


@pytest.mark.asyncio
async def test_reset_allows_re_fire():
    """After reset(), the watchdog can fire again for a new activation cycle."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(return_value=0)
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=1.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.12)
    assert release_cb.await_count == 1

    # Simulate: user activates gripper again, watchdog resets
    store._robot.gripper_activated_at = time.time() - 200
    wd.reset()

    await asyncio.sleep(0.12)
    await wd.stop()

    assert release_cb.await_count == 2


@pytest.mark.asyncio
async def test_vacuum_reader_exception_triggers_release():
    """If vacuum_reader raises, treat as sensor failure → release."""
    store = _make_store(gripper_active=True, activated_at=time.time() - 200)
    vacuum_reader = MagicMock(side_effect=RuntimeError("modbus timeout"))
    release_cb = AsyncMock()

    wd = GripperWatchdog(
        state_store_getter=lambda: store,
        vacuum_reader=vacuum_reader,
        release_callback=release_cb,
        timeout_s=1.0,
        rate_hz=50.0,
    )
    await wd.start()
    await asyncio.sleep(0.15)
    await wd.stop()

    release_cb.assert_awaited_once()


# ── StateStore integration ───────────────────────────────────────────────────

def test_store_sets_activated_at_on_gripper_active():
    """StateStore auto-sets gripper_activated_at when gripper_active=True."""
    store = StateStore()
    assert store._robot.gripper_activated_at is None

    store.set_robot(gripper_active=True)
    assert store._robot.gripper_active is True
    assert store._robot.gripper_activated_at is not None
    ts1 = store._robot.gripper_activated_at
    assert time.time() - ts1 < 1.0

    # Calling again should NOT overwrite (already set)
    store.set_robot(gripper_active=True)
    assert store._robot.gripper_activated_at == ts1


def test_store_clears_activated_at_on_gripper_inactive():
    """StateStore clears gripper_activated_at when gripper_active=False."""
    store = StateStore()
    store.set_robot(gripper_active=True)
    assert store._robot.gripper_activated_at is not None

    store.set_robot(gripper_active=False)
    assert store._robot.gripper_active is False
    assert store._robot.gripper_activated_at is None


def test_store_snapshot_includes_activated_at():
    """get_robot_snapshot() includes the new gripper_activated_at field."""
    store = StateStore()
    store.set_robot(gripper_active=True)

    snap = store.get_robot_snapshot()
    assert snap.gripper_activated_at is not None
    assert snap.gripper_active is True
