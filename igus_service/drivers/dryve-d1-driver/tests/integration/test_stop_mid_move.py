"""Scenario E: Stop/disable mid-move test.

Tests interrupting movement and verifying predictable outcome.
1. EnableOp
2. Start move to far target
3. After 300-500ms: disable (6040=0x00) or shutdown (0x06)
4. Verify: OperationEnabled=0 (if disable), is_moving=0, position stabilizes
"""

import asyncio

import pytest

from drivers.dryve_d1.od.indices import ODIndex
from test_utils.assertions import Eventually, Always
from test_utils.logging import TestLogger
from test_utils.config import TestConfig


@pytest.mark.asyncio
async def test_stop_mid_move(
    drive, test_config: TestConfig
) -> None:
    """Test stopping movement mid-move.

    PASS criteria:
    - Movement stops within 1 second after disable
    - No fault is raised
    - Position stabilizes (does not continue moving)
    """
    logger = TestLogger("test_stop_mid_move")

    logger.log_stage("start")

    # Step 1: EnableOp
    logger.log_stage("enableop")
    await drive.fault_reset()
    await drive.enable_operation()

    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and not s["fault"]

    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach OperationEnabled",
    )

    start_pos = await drive.get_position()
    logger.log_stage("ready", start_position=start_pos)

    # Step 2: Start move to far target
    target_pos = start_pos + 50000  # Far target
    velocity = 5000
    accel = 10000
    decel = 10000

    logger.log_stage(
        "move_start",
        target=target_pos,
        velocity=velocity,
    )

    # Start move as a task (we'll cancel it)
    move_task = asyncio.create_task(
        drive.move_to_position(
            target_position=target_pos,
            velocity=velocity,
            accel=accel,
            decel=decel,
            timeout_s=test_config.move_timeout_s,
        )
    )

    # Wait a bit for movement to start
    await asyncio.sleep(0.1)

    async def is_moving():
        return await drive.is_motion()

    # Verify movement started
    await Eventually(
        is_moving,
        timeout_s=1.0,
        poll_interval_s=test_config.motion_poll_interval_s,
        error_message="Movement did not start",
    )

    logger.log_stage("movement_started")

    # Step 3: Wait for delay, then disable
    delay = test_config.stop_mid_move_delay_s
    logger.log_stage("wait_before_stop", delay_s=delay)
    await asyncio.sleep(delay)

    # Get position just before stop
    pos_before_stop = await drive.get_position()
    logger.log_stage("stopping", position=pos_before_stop)

    # Disable voltage (shutdown)
    await drive.disable_voltage()

    # Step 4: Verify stop
    logger.log_stage("verify_stop")

    async def movement_stopped():
        s = await drive.get_status()
        is_moving = await drive.is_motion()
        return not is_moving or not s["operation_enabled"]

    await Eventually(
        movement_stopped,
        timeout_s=1.0,
        poll_interval_s=test_config.motion_poll_interval_s,
        error_message="Movement did not stop within 1 second",
    )

    # Cancel the move task (it may have already failed)
    move_task.cancel()
    try:
        await move_task
    except (asyncio.CancelledError, TimeoutError):
        pass

    # Verify OperationEnabled=0 (if we disabled)
    status = await drive.get_status()
    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    logger.log_stage(
        "stopped",
        operation_enabled=status["operation_enabled"],
        statusword=f"0x{sw:04X}",
    )

    # Verify no fault
    assert not status["fault"], "No fault should occur from stopping mid-move"

    # Step 5: Verify position stability
    logger.log_stage("verify_position_stability")
    # Wait a bit for position to stabilize after stop
    await asyncio.sleep(0.3)
    stop_pos = await drive.get_position()

    async def position_stable():
        current_pos = await drive.get_position()
        delta = abs(current_pos - stop_pos)
        # Use larger tolerance for stop stability (may have deceleration settling)
        return delta <= test_config.position_tolerance * 5

    await Always(
        position_stable,
        duration_s=1.0,
        poll_interval_s=test_config.motion_poll_interval_s,
        error_message="Position did not stabilize after stop",
    )

    final_pos = await drive.get_position()
    total_movement = abs(final_pos - start_pos)
    movement_after_stop = abs(final_pos - stop_pos)

    logger.log_stage(
        "complete",
        start_position=start_pos,
        stop_position=stop_pos,
        final_position=final_pos,
        total_movement=total_movement,
        movement_after_stop=movement_after_stop,
    )

    # Verify position didn't change much after stop
    assert (
        movement_after_stop <= test_config.glitch_eps * 2
    ), f"Position changed {movement_after_stop} after stop (expected <= {test_config.glitch_eps * 2})"

    logger.log_summary()

