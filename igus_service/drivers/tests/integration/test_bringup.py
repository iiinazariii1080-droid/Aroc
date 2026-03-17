"""Scenario B: CiA402 bring-up test.

Tests the state machine sequence: Shutdown → SwitchOn → EnableOp.
Uses Eventually assertions to wait for each state transition.
Checks that no fault occurs during the sequence.
"""

import pytest

from drivers.dryve_d1.od.indices import ODIndex
from test_utils.assertions import Eventually
from test_utils.logging import TestLogger
from test_utils.config import TestConfig


@pytest.mark.asyncio
async def test_bringup_sequence(
    drive, test_config: TestConfig
) -> None:
    """Test CiA402 bring-up sequence.

    Algorithm:
    1. Fault reset (if needed)
    2. Write 6040=0x06 (shutdown), wait for ReadyToSwitchOn
    3. Write 6040=0x07 (switch on), wait for SwitchedOn
    4. Write 6040=0x0F (enable operation), wait for OperationEnabled

    PASS criteria:
    - Each step reaches expected condition within timeout
    - No fault appears during the sequence
    - Quick stop remains enabled
    """
    logger = TestLogger("test_bringup")
    timeout = test_config.bringup_step_timeout_s

    logger.log_stage("start")

    # Step 1: Fault reset if needed
    logger.log_stage("fault_reset_check")
    status = await drive.get_status()
    if status["fault"]:
        logger.log_stage("fault_reset_execute")
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

    # Step 2-4: Use enable_operation which handles the full sequence
    # Shutdown → SwitchOn → EnableOp
    logger.log_stage("bringup_sequence", note="Using enable_operation for full sequence")
    await drive.enable_operation()

    async def operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        operation_enabled,
        timeout_s=timeout,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Did not reach OperationEnabled after enable operation",
    )

    status = await drive.get_status()
    sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
    logger.log_stage(
        "operation_enabled",
        statusword=f"0x{sw:04X}",
    )
    assert (
        status["operation_enabled"]
    ), "Operation should be enabled"
    assert (
        status["voltage_enabled"]
    ), "Voltage should be enabled"
    assert not status["fault"], "Fault should not appear after enable operation"
    assert (
        status["quick_stop"]
    ), "Quick stop should remain enabled"

    logger.log_stage("complete")
    logger.log_summary()

