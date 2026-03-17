"""Tests for IdleShutdownMixin two-phase lifecycle.

Verifies: schedule → fire, schedule → cancel, re-schedule, jog blocks shutdown.
Uses real asyncio event loop with short delays — no mocks for timing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from drivers.dryve_d1.api.idle_shutdown import IdleShutdownMixin
from drivers.dryve_d1.od.statusword import CiA402State


class FakeStateMachine:
    """Minimal CiA402StateMachine stub for idle shutdown tests."""

    def __init__(self, state: CiA402State = CiA402State.OPERATION_ENABLED) -> None:
        self._state = state

    async def current_state(self) -> CiA402State:
        return self._state


@dataclass
class FakeJogState:
    active: bool = False


class FakeJog:
    def __init__(self, *, active: bool = False) -> None:
        self.state = FakeJogState(active=active)


class IdleShutdownHarness(IdleShutdownMixin):
    """Concrete class mixing in IdleShutdownMixin for testing."""

    def __init__(self, *, delay_s: float = 0.1, jog_active: bool = False) -> None:
        self._idle_shutdown_handle: asyncio.TimerHandle | None = None
        self._idle_shutdown_task: asyncio.Task[None] | None = None
        self._idle_shutdown_delay_s = delay_s
        self._sm = FakeStateMachine()
        self._jog = FakeJog(active=jog_active)
        self.disable_voltage_called = False

    async def disable_voltage(self) -> None:
        self.disable_voltage_called = True


@pytest.mark.asyncio
async def test_idle_shutdown_fires_after_delay() -> None:
    """disable_voltage must be called after idle delay."""
    obj = IdleShutdownHarness(delay_s=0.1)

    obj._schedule_idle_shutdown()

    # Before delay — not yet called
    await asyncio.sleep(0.05)
    assert not obj.disable_voltage_called

    # After delay — must be called
    await asyncio.sleep(0.1)
    assert obj.disable_voltage_called, "disable_voltage should fire after idle delay"


@pytest.mark.asyncio
async def test_idle_shutdown_cancelled_by_new_motion() -> None:
    """Cancelling before delay expires must prevent disable_voltage."""
    obj = IdleShutdownHarness(delay_s=0.1)

    obj._schedule_idle_shutdown()
    await asyncio.sleep(0.05)
    obj._cancel_idle_shutdown_timer()  # new motion arrives

    await asyncio.sleep(0.15)
    assert not obj.disable_voltage_called, "disable_voltage should NOT fire after cancel"


@pytest.mark.asyncio
async def test_idle_shutdown_reschedule_resets_timer() -> None:
    """Re-scheduling must reset the delay timer."""
    obj = IdleShutdownHarness(delay_s=0.1)

    obj._schedule_idle_shutdown()
    await asyncio.sleep(0.05)

    # Re-schedule resets the timer
    obj._schedule_idle_shutdown()
    await asyncio.sleep(0.05)
    assert not obj.disable_voltage_called, "Timer should have been reset"

    # Wait for full delay from re-schedule
    await asyncio.sleep(0.1)
    assert obj.disable_voltage_called, "disable_voltage should fire after re-scheduled delay"


@pytest.mark.asyncio
async def test_idle_shutdown_blocked_by_active_jog() -> None:
    """Active jog must prevent disable_voltage even after delay."""
    obj = IdleShutdownHarness(delay_s=0.1, jog_active=True)

    obj._schedule_idle_shutdown()
    await asyncio.sleep(0.2)

    assert not obj.disable_voltage_called, "disable_voltage should NOT fire while jog is active"


@pytest.mark.asyncio
async def test_idle_shutdown_skips_in_low_power_state() -> None:
    """Already in SWITCH_ON_DISABLED → disable_voltage should not fire."""
    obj = IdleShutdownHarness(delay_s=0.1)
    obj._sm = FakeStateMachine(state=CiA402State.SWITCH_ON_DISABLED)

    obj._schedule_idle_shutdown()
    await asyncio.sleep(0.2)

    assert not obj.disable_voltage_called, "disable_voltage should NOT fire in low-power state"
