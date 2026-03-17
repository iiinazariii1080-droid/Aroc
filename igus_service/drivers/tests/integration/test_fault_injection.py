"""Scenario C: Fault injection and reset test.

Tests that fault properly "sticks" and can be reset.
1. Bring drive to EnableOp
2. Inject fault (6040=0x08 or emergency)
3. Verify fault appears and OperationEnabled=0
4. Verify movement does not start
5. Fault reset
6. Verify fault clears and bring-up works again
"""

import pytest

from drivers.dryve_d1.od.controlword import cw_clear_bits, CWBit
from drivers.dryve_d1.od.indices import ODIndex
from test_utils.assertions import Eventually, Always
from test_utils.monitors import CiA402InvariantMonitor
from test_utils.logging import TestLogger
from test_utils.config import TestConfig


@pytest.mark.asyncio
async def test_fault_injection_and_reset(
    drive, test_config: TestConfig
) -> None:
    """Test fault injection and reset sequence.

    PASS criteria:
    - Fault appears after injection
    - OperationEnabled becomes 0
    - Movement does not start when fault is active
    - Fault clears only after reset
    - Bring-up works after reset
    """
    logger = TestLogger("test_fault_injection")
    invariant_monitor = CiA402InvariantMonitor(
        transient_allowance_s=test_config.invariant_transient_allowance_s
    )

    logger.log_stage("start")

    # Step 1: Bring to EnableOp
    logger.log_stage("bringup_to_enableop")
    await drive.fault_reset()  # Clear any existing fault
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

    # Get initial position for stability check
    initial_pos = await drive.get_position()
    logger.log_stage("enableop_reached", position=initial_pos)

    # Step 2: Inject fault
    # Note: Fault injection methods vary by drive. Some drives may not support
    # fault injection via controlword manipulation, or may require specific conditions.
    # This test attempts to inject fault by clearing quick stop bit, but if this
    # doesn't work on the target drive, the test may need to be skipped or use
    # a drive-specific fault injection method.
    logger.log_stage("fault_injection", method="clear_quick_stop")
    
    # Read current controlword
    current_cw = await drive.read_u16(int(ODIndex.CONTROLWORD), 0)
    logger.log_stage("current_controlword", controlword=f"0x{current_cw:04X}")
    
    # Clear quick stop bit (bit 5)
    fault_cw = cw_clear_bits(current_cw, CWBit.QUICK_STOP)
    await drive.write_u16(int(ODIndex.CONTROLWORD), fault_cw, 0)
    logger.log_stage("fault_controlword_sent", controlword=f"0x{fault_cw:04X}")
    
    # Give the drive time to react (may need longer for some drives)
    import asyncio
    await asyncio.sleep(0.5)
    
    # Check if quick stop was triggered (may not always result in fault)
    status_after_injection = await drive.get_status()
    sw_after = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    logger.log_stage(
        "status_after_injection",
        fault=status_after_injection["fault"],
        quick_stop=status_after_injection["quick_stop"],
        operation_enabled=status_after_injection["operation_enabled"],
        statusword=f"0x{sw_after:04X}",
    )
    
    # If quick stop was triggered but not fault, that's also acceptable for this test
    # (quick stop is a safety mechanism, not necessarily a fault)
    if not status_after_injection["fault"] and not status_after_injection["quick_stop"]:
        # Neither fault nor quick stop - skip test if fault injection is not supported
        pytest.skip(
            "Fault injection via quick stop bit clear did not trigger fault or quick stop. "
            "This drive may not support this fault injection method."
        )

    # Step 3: Wait for fault to appear
    logger.log_stage("wait_for_fault")
    
    async def fault_active():
        s = await drive.get_status()
        return s["fault"]

    await Eventually(
        fault_active,
        timeout_s=test_config.fault_reset_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Fault did not appear after injection",
    )

    status = await drive.get_status()
    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    logger.log_stage(
        "fault_detected",
        statusword=f"0x{sw:04X}",
    )
    assert status["fault"], "Fault should be active"
    assert (
        not status["operation_enabled"]
    ), "OperationEnabled should be 0 when fault is active"

    # Step 4: Verify movement does not start
    logger.log_stage("verify_no_movement")
    
    # Try to start a move (this should not work)
    target_pos = initial_pos + 10000
    try:
        await drive.move_to_position(
            target_position=target_pos,
            velocity=1000,
            accel=1000,
            decel=1000,
            timeout_s=1.0,  # Short timeout since it should fail
        )
        # If we get here, the move started (unexpected)
        logger.log_stage("warning", message="Move started despite fault")
    except (TimeoutError, RuntimeError):
        # Expected: move should not start
        pass

    # Check position stability (should not move)
    async def position_stable():
        current_pos = await drive.get_position()
        return abs(current_pos - initial_pos) <= test_config.glitch_eps

    await Always(
        position_stable,
        duration_s=1.0,
        poll_interval_s=test_config.motion_poll_interval_s,
        error_message="Position changed during fault (should be stable)",
    )

    # Step 5: Fault reset
    logger.log_stage("fault_reset")
    await drive.fault_reset()

    async def fault_cleared():
        s = await drive.get_status()
        return not s["fault"]

    await Eventually(
        fault_cleared,
        timeout_s=test_config.fault_reset_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Fault did not clear after reset",
    )

    status = await drive.get_status()
    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    logger.log_stage(
        "fault_cleared",
        statusword=f"0x{sw:04X}",
    )
    assert not status["fault"], "Fault should be cleared"

    # Step 6: Verify bring-up works again
    logger.log_stage("verify_bringup_after_reset")
    await drive.enable_operation()

    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach OperationEnabled after fault reset",
    )

    logger.log_stage("complete")
    logger.log_summary()

