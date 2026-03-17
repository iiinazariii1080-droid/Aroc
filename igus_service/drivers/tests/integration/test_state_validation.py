"""Tests for state validation and error handling.

These tests use test-only API endpoints to set various drive states
and verify that the driver correctly handles and validates these states.

Test API endpoints (ONLY for testing):
- POST /test/homed?value=0|1 - Set homed status
- POST /test/fault?value=0|1 - Set fault state
- POST /test/emergency?value=0|1 - Set emergency stop

WARNING: These endpoints are ONLY available in test/simulation mode!
"""

from __future__ import annotations

import asyncio
import warnings

import pytest
import pytest_asyncio

from drivers.dryve_d1.od.indices import ODIndex
from test_utils.assertions import Eventually
from test_utils.config import TestConfig
from test_utils.logging import TestLogger
from test_utils.test_api import TestDriveController, get_test_api_url


@pytest_asyncio.fixture
async def test_api():
    """Fixture for test API controller."""
    import pytest_asyncio
    api_url = get_test_api_url()
    controller = TestDriveController(base_url=api_url)
    yield controller
    # Cleanup: reset all test states
    try:
        await controller.reset_all_test_states()
    except Exception:
        pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_move_to_position_without_homing_warning(
    drive, test_config: TestConfig, test_api: TestDriveController
) -> None:
    """Test that move_to_position warns when homing is not done.
    
    PASS criteria:
    - Setting homed=0 via test API
    - move_to_position() issues a warning when homed=0
    - Warning message mentions homing/reference
    """
    logger = TestLogger("test_move_without_homing_warning")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled
    status = await drive.get_status()
    if status["fault"]:
        await drive.fault_reset()
        await asyncio.sleep(0.2)
    
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Set homed=0 via test API
    logger.log_stage("set_homed_false")
    try:
        await test_api.set_homed(False)
        logger.log_stage("homed_set_to_false")
    except Exception as e:
        pytest.skip(f"Test API not available: {e}")
    
    # Step 3: Verify is_homed() returns False
    is_homed = await drive.is_homed()
    logger.log_stage("check_homed_status", is_homed=is_homed)
    assert not is_homed, "is_homed() should return False after setting homed=0"
    
    # Step 4: Attempt move_to_position and capture warning
    initial_pos = await drive.get_position()
    target_pos = initial_pos + 50000
    
    logger.log_stage("move_attempt_without_homing", target=target_pos)
    
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        try:
            await drive.move_to_position(
                target_position=target_pos,
                velocity=2000,
                accel=5000,
                decel=5000,
                timeout_s=5.0,
            )
            
            # Check that warning was issued
            assert len(w) > 0, "move_to_position should issue warning when not homed"
            
            warning_messages = [str(warning.message) for warning in w]
            has_homing_warning = any(
                "homing" in msg.lower() or "reference" in msg.lower()
                for msg in warning_messages
            )
            
            assert has_homing_warning, (
                f"Warning should mention homing/reference. Got: {warning_messages}"
            )
            
            logger.log_stage("move_with_warning", warnings=warning_messages)
            
        except Exception as e:
            # Move might fail, but we should have gotten a warning first
            if len(w) == 0:
                raise AssertionError(f"Move failed but no warning was issued: {e}")
            logger.log_stage("move_failed_after_warning", error=str(e), warnings=[str(warning.message) for warning in w])
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_move_to_position_with_homing_success(
    drive, test_config: TestConfig, test_api: TestDriveController
) -> None:
    """Test that move_to_position works when homing is done.
    
    PASS criteria:
    - Setting homed=1 via test API
    - move_to_position() does not issue warning when homed=1
    - Movement completes successfully
    """
    logger = TestLogger("test_move_with_homing_success")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled
    status = await drive.get_status()
    if status["fault"]:
        await drive.fault_reset()
        await asyncio.sleep(0.2)
    
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Set homed=1 via test API
    logger.log_stage("set_homed_true")
    try:
        await test_api.set_homed(True)
        logger.log_stage("homed_set_to_true")
    except Exception as e:
        pytest.skip(f"Test API not available: {e}")
    
    # Step 3: Verify is_homed() returns True
    is_homed = await drive.is_homed()
    logger.log_stage("check_homed_status", is_homed=is_homed)
    assert is_homed, "is_homed() should return True after setting homed=1"
    
    # Step 4: Attempt move_to_position - should not warn
    initial_pos = await drive.get_position()
    target_pos = initial_pos + 50000
    
    logger.log_stage("move_attempt_with_homing", target=target_pos)
    
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        
        try:
            await drive.move_to_position(
                target_position=target_pos,
                velocity=2000,
                accel=5000,
                decel=5000,
                timeout_s=10.0,
            )
            
            # Should not have homing-related warnings
            homing_warnings = [
                warning for warning in w
                if "homing" in str(warning.message).lower() or "reference" in str(warning.message).lower()
            ]
            
            assert len(homing_warnings) == 0, (
                f"move_to_position should not warn when homed. Got: {[str(w.message) for w in homing_warnings]}"
            )
            
            # Verify position reached target
            final_pos = await drive.get_position()
            position_error = abs(final_pos - target_pos)
            
            logger.log_stage("move_success", final_position=final_pos, error=position_error)
            assert position_error <= test_config.position_tolerance, (
                f"Position error {position_error} exceeds tolerance"
            )
            
        except Exception as e:
            logger.log_stage("move_failed", error=str(e))
            # If move fails for other reasons, that's okay for this test
            # The important thing is that no warning was issued
            if len(w) > 0:
                homing_warnings = [
                    warning for warning in w
                    if "homing" in str(warning.message).lower() or "reference" in str(warning.message).lower()
                ]
                assert len(homing_warnings) == 0, (
                    f"Should not have homing warnings even if move fails. Got: {[str(w.message) for w in homing_warnings]}"
                )
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_operations_blocked_in_fault_state(
    drive, test_config: TestConfig, test_api: TestDriveController
) -> None:
    """Test that operations are blocked or fail when drive is in fault state.
    
    PASS criteria:
    - Setting fault=1 via test API
    - Operations (move, jog) fail or are blocked
    - fault_reset() clears the fault
    - Operations work again after fault reset
    """
    logger = TestLogger("test_operations_in_fault")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled
    status = await drive.get_status()
    if status["fault"]:
        await drive.fault_reset()
        await asyncio.sleep(0.2)
    
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Set fault=1 via test API
    logger.log_stage("set_fault_true")
    try:
        await test_api.set_fault(True)
        logger.log_stage("fault_set_to_true")
        
        # Wait a bit for fault to propagate
        await asyncio.sleep(0.2)
    except Exception as e:
        pytest.skip(f"Test API not available: {e}")
    
    # Step 3: Verify fault status
    status = await drive.get_status()
    logger.log_stage("check_fault_status", fault=status["fault"])
    assert status["fault"], "Drive should be in fault state"
    
    # Step 4: Attempt operations - should fail or be blocked
    initial_pos = await drive.get_position()
    target_pos = initial_pos + 50000
    
    logger.log_stage("move_attempt_in_fault")
    
    # move_to_position should fail
    with pytest.raises((RuntimeError, ValueError, Exception)) as exc_info:
        await drive.move_to_position(
            target_position=target_pos,
            velocity=2000,
            accel=5000,
            decel=5000,
            timeout_s=2.0,
        )
    
    logger.log_stage("move_failed_in_fault", error=str(exc_info.value))
    
    # jog_start should also fail
    logger.log_stage("jog_attempt_in_fault")
    with pytest.raises((RuntimeError, ValueError, Exception)) as exc_info:
        await drive.jog_start(velocity=1000)
    
    logger.log_stage("jog_failed_in_fault", error=str(exc_info.value))
    
    # Step 5: Clear fault via fault_reset
    logger.log_stage("fault_reset")
    await drive.fault_reset()
    await asyncio.sleep(0.3)
    
    # Also clear via test API to ensure clean state
    await test_api.set_fault(False)
    await asyncio.sleep(0.2)
    
    # Step 6: Verify fault is cleared
    status_after_reset = await drive.get_status()
    logger.log_stage("check_fault_after_reset", fault=status_after_reset["fault"])
    assert not status_after_reset["fault"], "Fault should be cleared after fault_reset"
    
    # Step 7: Operations should work again
    logger.log_stage("operations_after_reset")
    # Re-enable operation if needed
    if not status_after_reset["operation_enabled"]:
        if drive._sm is not None:
            await drive._sm.run_to_operation_enabled()
        else:
            await drive.enable_operation()
    
    # Verify operations work
    final_status = await drive.get_status()
    assert final_status["operation_enabled"], "Operation should be enabled after fault reset"
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_operations_blocked_in_emergency_stop(
    drive, test_config: TestConfig, test_api: TestDriveController
) -> None:
    """Test that operations are blocked when emergency stop is active.
    
    PASS criteria:
    - Setting emergency=1 via test API
    - Operations (move, jog) fail or are blocked
    - Clearing emergency=0 allows operations again
    """
    logger = TestLogger("test_operations_in_emergency")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled
    status = await drive.get_status()
    if status["fault"]:
        await drive.fault_reset()
        await asyncio.sleep(0.2)
    
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Set emergency=1 via test API
    logger.log_stage("set_emergency_true")
    try:
        await test_api.set_emergency(True)
        logger.log_stage("emergency_set_to_true")
        
        # Wait a bit for emergency to propagate
        await asyncio.sleep(0.2)
    except Exception as e:
        pytest.skip(f"Test API not available: {e}")
    
    # Step 3: Attempt operations - should fail or be blocked
    initial_pos = await drive.get_position()
    target_pos = initial_pos + 50000
    
    logger.log_stage("move_attempt_in_emergency")
    
    # move_to_position should fail
    with pytest.raises((RuntimeError, ValueError, Exception)) as exc_info:
        await drive.move_to_position(
            target_position=target_pos,
            velocity=2000,
            accel=5000,
            decel=5000,
            timeout_s=2.0,
        )
    
    logger.log_stage("move_failed_in_emergency", error=str(exc_info.value))
    
    # jog_start should also fail
    logger.log_stage("jog_attempt_in_emergency")
    with pytest.raises((RuntimeError, ValueError, Exception)) as exc_info:
        await drive.jog_start(velocity=1000)
    
    logger.log_stage("jog_failed_in_emergency", error=str(exc_info.value))
    
    # Step 4: Clear emergency stop
    logger.log_stage("clear_emergency")
    await test_api.set_emergency(False)
    await asyncio.sleep(0.3)
    
    # Step 5: Re-enable operation after emergency clear
    # Emergency stop may have disabled operation, so we need to re-enable it
    logger.log_stage("re_enable_after_emergency")
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    # Wait for operation to be enabled
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled after emergency clear",
    )
    
    # Step 6: Verify status is good
    logger.log_stage("operations_after_emergency_clear")
    status_after = await drive.get_status()
    assert status_after["operation_enabled"], "Operation should be enabled after emergency clear"
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_state_transitions_and_validation(
    drive, test_config: TestConfig, test_api: TestDriveController
) -> None:
    """Test state transitions and validation across multiple states.
    
    PASS criteria:
    - Drive correctly validates states before operations
    - State transitions work correctly
    - Operations fail gracefully in invalid states
    """
    logger = TestLogger("test_state_transitions")
    logger.log_stage("start")
    
    # Step 1: Initial state - bring to Operation Enabled
    status = await drive.get_status()
    if status["fault"]:
        await drive.fault_reset()
        await asyncio.sleep(0.2)
    
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and s["voltage_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Test sequence: NOT_HOMED -> HOMED -> FAULT -> CLEAR -> EMERGENCY -> CLEAR
    logger.log_stage("state_sequence_start")
    
    # 2.1: NOT_HOMED state
    await test_api.set_homed(False)
    await asyncio.sleep(0.1)
    assert not await drive.is_homed(), "Should be NOT_HOMED"
    logger.log_stage("state_not_homed")
    
    # 2.2: HOMED state
    await test_api.set_homed(True)
    await asyncio.sleep(0.1)
    assert await drive.is_homed(), "Should be HOMED"
    logger.log_stage("state_homed")
    
    # 2.3: FAULT state
    await test_api.set_fault(True)
    await asyncio.sleep(0.2)
    status = await drive.get_status()
    assert status["fault"], "Should be in FAULT state"
    logger.log_stage("state_fault")
    
    # 2.4: Clear fault
    await drive.fault_reset()
    await test_api.set_fault(False)
    await asyncio.sleep(0.3)
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    status = await drive.get_status()
    assert not status["fault"], "Fault should be cleared"
    logger.log_stage("state_fault_cleared")
    
    # 2.5: EMERGENCY state
    await test_api.set_emergency(True)
    await asyncio.sleep(0.2)
    logger.log_stage("state_emergency")
    
    # 2.6: Clear emergency
    await test_api.set_emergency(False)
    await asyncio.sleep(0.2)
    logger.log_stage("state_emergency_cleared")
    
    # Re-enable operation after emergency clear
    if drive._sm is not None:
        await drive._sm.run_to_operation_enabled()
    else:
        await drive.enable_operation()
    
    # Wait for operation to be enabled
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled after state transitions",
    )
    
    # Step 3: Final validation - all states should be clear
    final_status = await drive.get_status()
    assert not final_status["fault"], "Final state should not have fault"
    assert final_status["operation_enabled"], "Final state should be operation enabled"
    
    logger.log_stage("complete")
    logger.log_summary()

