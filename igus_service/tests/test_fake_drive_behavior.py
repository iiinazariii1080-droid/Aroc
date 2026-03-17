"""Tests for FakeDrive behavioral contracts.

Verifies that FakeDrive correctly tracks: is_moving state, fault_reset recovery,
and fault gate enforcement — matching real DryveD1 behavioral contracts.
"""

from __future__ import annotations

import pytest

from drivers.dryve_d1.od.statusword import CiA402State
from tests.fakes import FakeDrive


_VALID_MOVE = dict(target_position=1000, velocity=100, accel=50, decel=50)
_VALID_JOG = dict(velocity=100, ttl_ms=500)


@pytest.mark.asyncio
async def test_is_moving_true_after_move_to_position() -> None:
    """is_moving() must return True after move_to_position, False after stop."""
    drive = FakeDrive()
    assert await drive.is_moving() is False

    await drive.move_to_position(**_VALID_MOVE)
    assert await drive.is_moving() is True

    await drive.stop()
    assert await drive.is_moving() is False


@pytest.mark.asyncio
async def test_is_moving_true_during_jog() -> None:
    """is_moving() must return True after jog_start, False after jog_stop."""
    drive = FakeDrive()
    await drive.jog_start(**_VALID_JOG)
    assert await drive.is_moving() is True

    await drive.jog_stop()
    assert await drive.is_moving() is False


@pytest.mark.asyncio
async def test_fault_reset_recover_true_reaches_operation_enabled() -> None:
    """fault_reset(recover=True) must transition FAULT → OPERATION_ENABLED."""
    drive = FakeDrive(fault_mode=True)
    assert drive._cia402_state == CiA402State.FAULT

    await drive.fault_reset(recover=True)
    assert drive._cia402_state == CiA402State.OPERATION_ENABLED


@pytest.mark.asyncio
async def test_fault_reset_recover_false_stays_switch_on_disabled() -> None:
    """fault_reset(recover=False) must transition FAULT → SWITCH_ON_DISABLED."""
    drive = FakeDrive(fault_mode=True)
    await drive.fault_reset(recover=False)
    assert drive._cia402_state == CiA402State.SWITCH_ON_DISABLED


@pytest.mark.asyncio
async def test_motion_blocked_in_fault_state() -> None:
    """move_to_position must raise RuntimeError when drive is in FAULT."""
    drive = FakeDrive(fault_mode=True)
    with pytest.raises(RuntimeError, match="FAULT"):
        await drive.move_to_position(**_VALID_MOVE)


@pytest.mark.asyncio
async def test_jog_blocked_in_fault_state() -> None:
    """jog_start must raise RuntimeError when drive is in FAULT."""
    drive = FakeDrive(fault_mode=True)
    with pytest.raises(RuntimeError, match="FAULT"):
        await drive.jog_start(**_VALID_JOG)
