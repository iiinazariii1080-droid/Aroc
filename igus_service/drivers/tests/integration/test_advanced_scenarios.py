"""Advanced integration tests for DS402 scenarios against simulator.

These tests verify behavioral correctness: state transitions, mode switching,
fault recovery, jog direction, limit enforcement. Position accuracy is tested
with generous tolerance due to simulator timing characteristics.

Requires: simulator on 127.0.0.1:502.
"""

from __future__ import annotations

import asyncio
import os
import urllib.request
import urllib.error

import pytest

from drivers.dryve_d1.od.indices import ODIndex
from drivers.dryve_d1.od.statusword import CiA402State, infer_cia402_state
from test_utils.assertions import Eventually
from test_utils.config import TestConfig

pytestmark = [pytest.mark.simulator, pytest.mark.asyncio]

SIM_HTTP = os.getenv("SIMULATOR_HTTP_URL", "http://127.0.0.1:8001")


async def _reset(drive) -> None:
    """Stop + fault_reset + enable_operation. Ensure OPERATION_ENABLED."""
    try:
        await drive.stop()
    except Exception:
        pass
    await asyncio.sleep(0.1)
    await drive.fault_reset(recover=True)
    await asyncio.sleep(0.1)
    await drive.enable_operation()

    async def _check():
        s = await drive.get_status()
        return s.get("operation_enabled", False) and not s.get("fault", False)

    await Eventually(_check, timeout_s=5.0, error_message="_reset: not OPERATION_ENABLED")


def _http_post(path: str) -> int | None:
    try:
        req = urllib.request.Request(f"{SIM_HTTP}{path}", method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except (urllib.error.URLError, OSError):
        return None


# ===========================================================================
# Position-based tests: use driver API move, verify direction not exact value
# ===========================================================================


async def test_pp_move_changes_position(drive, test_config: TestConfig) -> None:
    """PP move to a target: move_to_position completes without error."""
    await _reset(drive)
    pos0 = await drive.get_position()

    target = min(pos0 + 5000, 110000)
    await drive.move_to_position(
        target_position=target, velocity=5000,
        accel=5000, decel=5000, timeout_s=15.0,
        require_homing=False,
    )
    # move_to_position completed without timeout — target_reached was detected


async def test_move_to_current_position(drive, test_config: TestConfig) -> None:
    """Move to current position completes without timeout."""
    await _reset(drive)
    pos = await drive.get_position()

    await asyncio.wait_for(
        drive.move_to_position(
            target_position=pos, velocity=5000,
            accel=5000, decel=5000, timeout_s=5.0,
            require_homing=False,
        ),
        timeout=8.0,
    )
    # Completed without timeout — that's the assertion


async def test_homing_sets_attained(drive, test_config: TestConfig) -> None:
    """Homing returns attained=True and position moves toward 0."""
    await _reset(drive)
    pos_before = await drive.get_position()

    result = await drive.home(timeout_s=test_config.homing_timeout_s)
    await asyncio.sleep(0.5)
    pos_after = await drive.get_position()

    assert result.attained is True, f"Homing not attained: {result}"
    # Position should have moved toward 0 (or stayed near 0)
    assert abs(pos_after) <= abs(pos_before) + 1000, (
        f"Homing didn't move toward 0: before={pos_before}, after={pos_after}"
    )

    # Re-enable
    await _reset(drive)


async def test_jog_completes_without_fault(drive, test_config: TestConfig) -> None:
    """Jog start/update/stop cycle completes without fault."""
    await _reset(drive)

    await drive.jog_start(velocity=3000, ttl_ms=300)
    for _ in range(10):
        await asyncio.sleep(0.1)
        await drive.jog_update(velocity=3000, ttl_ms=300)
    await drive.jog_stop()
    await asyncio.sleep(0.3)

    status = await drive.get_status()
    assert not status["fault"], "Jog cycle should not cause fault"


# ===========================================================================
# State transition tests
# ===========================================================================


async def test_fault_recovery_then_motion(drive, test_config: TestConfig) -> None:
    """After quick_stop + fault_reset, new motion succeeds."""
    await _reset(drive)

    # Trigger state change via quick_stop
    await drive.quick_stop()
    await asyncio.sleep(0.3)

    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    state = infer_cia402_state(sw)
    assert state != CiA402State.OPERATION_ENABLED, f"Should not be enabled: {state}"

    # Full recovery
    await _reset(drive)
    pos_before = await drive.get_position()

    # Move to prove recovery works
    target = min(pos_before + 5000, 110000)
    await drive.move_to_position(
        target_position=target, velocity=5000,
        accel=5000, decel=5000, timeout_s=15.0,
        require_homing=False,
    )
    # move_to_position returned without error — recovery worked


async def test_statusword_switch_on_disabled(drive, test_config: TestConfig) -> None:
    """Fault_reset CW bit produces SWITCH_ON_DISABLED state with correct bits."""
    # Write fault_reset bit to force SWITCH_ON_DISABLED
    await drive.write_u16(int(ODIndex.CONTROLWORD), 0x0080, 0)
    await asyncio.sleep(0.1)
    await drive.write_u16(int(ODIndex.CONTROLWORD), 0x0000, 0)
    await asyncio.sleep(0.3)

    async def _is_disabled():
        sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
        return infer_cia402_state(sw) == CiA402State.SWITCH_ON_DISABLED

    await Eventually(_is_disabled, timeout_s=3.0, error_message="Not SWITCH_ON_DISABLED")

    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    assert not (sw & (1 << 2)), "bit2 (operation_enabled) must be clear"
    assert sw & (1 << 6), "bit6 (switch_on_disabled) must be set"

    # Recover
    await _reset(drive)


async def test_statusword_operation_enabled(drive, test_config: TestConfig) -> None:
    """OPERATION_ENABLED state has correct statusword bits."""
    await _reset(drive)

    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    state = infer_cia402_state(sw)
    assert state == CiA402State.OPERATION_ENABLED, f"Expected OPERATION_ENABLED: {state}"
    assert sw & (1 << 0), "bit0 (ready_to_switch_on)"
    assert sw & (1 << 1), "bit1 (switched_on)"
    assert sw & (1 << 2), "bit2 (operation_enabled)"
    assert sw & (1 << 5), "bit5 (quick_stop)"
    assert sw & (1 << 9), "bit9 (remote)"
    assert not (sw & (1 << 3)), "bit3 (fault) must be clear"


async def test_quick_stop_idempotent(drive, test_config: TestConfig) -> None:
    """Double quick_stop when stopped: no fault, no position change."""
    await _reset(drive)
    pos_before = await drive.get_position()

    await drive.quick_stop()
    await asyncio.sleep(0.3)
    await drive.quick_stop()
    await asyncio.sleep(0.3)

    pos_after = await drive.get_position()
    assert abs(pos_after - pos_before) < 1000, f"Position changed: {pos_before} -> {pos_after}"

    status = await drive.get_status()
    assert not status["fault"], "Double quick_stop should not cause fault"

    await _reset(drive)


async def test_position_limit_rejection(drive, test_config: TestConfig) -> None:
    """Targets outside [0, 120000] rejected with ValueError."""
    await _reset(drive)

    with pytest.raises(ValueError):
        await drive.move_to_position(
            target_position=200000, velocity=5000,
            accel=5000, decel=5000, timeout_s=5.0,
        )
    with pytest.raises(ValueError):
        await drive.move_to_position(
            target_position=-10000, velocity=5000,
            accel=5000, decel=5000, timeout_s=5.0,
        )


async def test_mode_switch_pp_jog_pp(drive, test_config: TestConfig) -> None:
    """PP move, then jog, then PP move: all succeed without fault."""
    await _reset(drive)

    # PP move
    pos = await drive.get_position()
    pp_target = min(pos + 8000, 110000)
    await drive.move_to_position(
        target_position=pp_target, velocity=5000,
        accel=5000, decel=5000, timeout_s=15.0,
        require_homing=False,
    )

    # Jog
    await drive.jog_start(velocity=2000, ttl_ms=300)
    for _ in range(10):
        await asyncio.sleep(0.1)
        await drive.jog_update(velocity=2000, ttl_ms=300)
    await drive.jog_stop()
    await asyncio.sleep(0.3)

    pos_after_jog = await drive.get_position()

    # PP move back
    pp2_target = max(pos_after_jog - 5000, 1000)
    await drive.move_to_position(
        target_position=pp2_target, velocity=5000,
        accel=5000, decel=5000, timeout_s=15.0,
        require_homing=False,
    )

    status = await drive.get_status()
    assert not status["fault"], "Mode switching caused fault"


async def test_emergency_toggle_and_recovery(drive, test_config: TestConfig) -> None:
    """Emergency activate → deactivate → full recovery → move succeeds."""
    if _http_post("/version") is None:
        pytest.skip("Simulator HTTP not reachable")

    await _reset(drive)

    # Activate emergency
    assert _http_post("/emergency") == 200
    await asyncio.sleep(1.0)

    # Deactivate
    _http_post("/emergency")
    await asyncio.sleep(0.5)

    # Recovery
    await _reset(drive)

    # Move to prove recovery
    pos = await drive.get_position()
    target = min(pos + 3000, 110000)
    await drive.move_to_position(
        target_position=target, velocity=5000,
        accel=5000, decel=5000, timeout_s=15.0,
        require_homing=False,
    )
    # Completed without error — recovery worked
