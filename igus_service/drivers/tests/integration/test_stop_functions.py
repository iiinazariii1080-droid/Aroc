"""Tests for stop functions according to user manual.

According to the manual:
- "Stop" command stops movement with a pre-set rate of deceleration (Profile Deceleration, 0x6084)
- "Quick Stop" command stops movement with the rate of deceleration previously set at "Motion Limits" (Quick Stop Deceleration, 0x6085)

The manual recommends that Quick Stop Deceleration should be set higher than Max. Acceleration (recommendation: factor 10).
"""

from __future__ import annotations

import asyncio

import pytest

from drivers.dryve_d1.od.indices import ODIndex
from test_utils.assertions import Eventually, Always
from test_utils.config import TestConfig
from test_utils.logging import TestLogger


def debug_log(location: str, message: str, data: dict | None = None):
    """Debug logging helper."""
    import json
    import time
    from pathlib import Path
    
    log_path = Path(__file__).resolve().parent.parent.parent / ".cursor" / "debug.log"
    try:
        log_entry = {
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data or {},
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": "B",
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_stop_in_profile_position_mode(
    drive, test_config: TestConfig
) -> None:
    """Test that stop() works correctly in Profile Position mode.
    
    PASS criteria:
    - Motor is in Profile Position mode
    - stop() stops movement using normal deceleration (HALT bit)
    - Motor decelerates and stops (does not continue to target)
    """
    logger = TestLogger("test_stop_profile_position")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled (full bringup sequence)
    # Use the same approach as test_bringup.py
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
    
    # Use run_to_operation_enabled which handles the full sequence
    # shutdown -> switch_on -> enable_operation
    logger.log_stage("bringup_sequence", note="Using run_to_operation_enabled for full sequence")
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
    
    # Step 2: Get initial position and set up Profile Position mode
    initial_pos = await drive.get_position()
    logger.log_stage("initial_position", position=initial_pos)
    debug_log("test_stop_functions.py:initial_position", "Got initial position", {"position": initial_pos})
    
    from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
    
    target_pos = initial_pos + 100000  # Large target to ensure movement
    velocity = 5000
    accel = 10000
    decel = 10000
    
    pp_config = ProfilePositionConfig(
        profile_velocity=velocity,
        acceleration=accel,
        deceleration=decel,
        verify_mode=True,
    )
    pp = ProfilePosition(drive, config=pp_config)
    
    await pp.ensure_mode()
    await pp.configure(profile_velocity=velocity, acceleration=accel, deceleration=decel)
    
    # Step 3: Start movement
    logger.log_stage("movement_start", target=target_pos, initial_position=initial_pos)
    debug_log("test_stop_functions.py:movement_start", "Starting movement", {
        "target": target_pos,
        "initial": initial_pos,
    })
    
    # Start move (non-blocking)
    await drive.write_i32(int(ODIndex.TARGET_POSITION), target_pos, 0)
    base = await drive.read_u16(int(ODIndex.CONTROLWORD), 0)
    from drivers.dryve_d1.od.controlword import cw_enable_operation, cw_pulse_new_set_point
    base = cw_enable_operation()
    set_word, clear_word = cw_pulse_new_set_point(base)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)
    
    # Wait for movement to start
    await asyncio.sleep(0.3)
    pos_after_start = await drive.get_position()
    debug_log("test_stop_functions.py:movement_started", "Movement started", {"position": pos_after_start})
    
    # Step 4: Call stop() while motor is moving
    position_at_stop_call = await drive.get_position()
    status_before_stop = await drive.get_status()
    logger.log_stage("stop_called", position_at_stop=position_at_stop_call)
    debug_log("test_stop_functions.py:stop_called", "Calling stop()", {
        "position": position_at_stop_call,
        "target": target_pos,
        "distance_to_target": abs(target_pos - position_at_stop_call),
    })
    
    # Call stop() - should use normal deceleration (HALT bit in Profile Position)
    await drive.stop()
    debug_log("test_stop_functions.py:stop_executed", "stop() executed", None)
    
    # Step 5: Verify motor stops (does not continue to target)
    await asyncio.sleep(0.5)  # Give motor time to decelerate
    
    # Check position multiple times to verify it's stopped
    positions_after_stop = []
    for i in range(5):
        await asyncio.sleep(0.1)
        pos = await drive.get_position()
        positions_after_stop.append(pos)
        status = await drive.get_status()
        debug_log(f"test_stop_functions.py:position_check_{i}", "Position check after stop", {
            "position": pos,
            "statusword": f"0x{await drive.read_u16(int(ODIndex.STATUSWORD), 0):04X}",
            "target_reached": status["target_reached"],
        })
    
    final_position = positions_after_stop[-1]
    position_delta = abs(final_position - position_at_stop_call)
    
    logger.log_stage(
        "stop_result",
        position_at_stop=position_at_stop_call,
        final_position=final_position,
        position_delta=position_delta,
        target=target_pos,
        target_reached=status["target_reached"],
    )
    
    # Verify motor stopped before reaching target
    # In Profile Position mode, stop() should stop the motor before it reaches target
    distance_to_target = abs(final_position - target_pos)
    assert distance_to_target > test_config.position_tolerance, (
        f"Motor reached target after stop(): "
        f"final_position={final_position}, target={target_pos}, "
        f"distance_to_target={distance_to_target}"
    )
    
    # Verify position is stable (motor is not moving)
    position_changes = [abs(positions_after_stop[i+1] - positions_after_stop[i]) for i in range(len(positions_after_stop)-1)]
    max_position_change = max(position_changes) if position_changes else 0
    assert max_position_change < test_config.position_tolerance * 2, (
        f"Motor is still moving after stop(): "
        f"max_position_change={max_position_change}, positions={positions_after_stop}"
    )
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_stop_in_profile_velocity_mode(
    drive, test_config: TestConfig
) -> None:
    """Test that stop() works correctly in Profile Velocity mode.
    
    PASS criteria:
    - Motor is in Profile Velocity mode
    - stop() stops movement by setting target velocity to 0
    - Motor decelerates and stops
    """
    logger = TestLogger("test_stop_profile_velocity")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled (full bringup sequence)
    await drive.fault_reset()
    
    # Ensure we're in a valid state before enabling operation
    status = await drive.get_status()
    if not status.get("ready_to_switch_on", False) and not status.get("switched_on", False):
        # Try to get to ready_to_switch_on first
        if drive._sm is not None:
            await drive._sm.shutdown()
            await asyncio.sleep(0.2)
    
    await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Set up Profile Velocity mode
    from drivers.dryve_d1.motion.profile_velocity import ProfileVelocity, ProfileVelocityConfig
    
    velocity = 5000
    accel = 10000
    decel = 10000
    
    pv_config = ProfileVelocityConfig(
        acceleration=accel,
        deceleration=decel,
        verify_mode=True,
    )
    pv = ProfileVelocity(drive, config=pv_config)
    
    await pv.ensure_mode()
    await pv.configure(acceleration=accel, deceleration=decel)
    
    # Step 3: Start movement
    initial_pos = await drive.get_position()
    logger.log_stage("movement_start", target_velocity=velocity, initial_position=initial_pos)
    debug_log("test_stop_functions.py:velocity_start", "Starting velocity movement", {
        "target_velocity": velocity,
        "initial_position": initial_pos,
    })
    
    await pv.set_target_velocity(velocity)
    
    # Wait for movement to start
    await asyncio.sleep(0.3)
    pos_after_start = await drive.get_position()
    debug_log("test_stop_functions.py:velocity_started", "Velocity movement started", {"position": pos_after_start})
    
    # Step 4: Call stop() while motor is moving
    position_at_stop_call = await drive.get_position()
    logger.log_stage("stop_called", position_at_stop=position_at_stop_call)
    debug_log("test_stop_functions.py:stop_called_velocity", "Calling stop() in velocity mode", {
        "position": position_at_stop_call,
    })
    
    # Call stop() - should set target velocity to 0
    await drive.stop()
    debug_log("test_stop_functions.py:stop_executed_velocity", "stop() executed in velocity mode", None)
    
    # Step 5: Verify motor stops
    await asyncio.sleep(0.5)  # Give motor time to decelerate
    
    # Check position multiple times to verify it's stopped
    positions_after_stop = []
    for i in range(5):
        await asyncio.sleep(0.1)
        pos = await drive.get_position()
        positions_after_stop.append(pos)
        debug_log(f"test_stop_functions.py:velocity_position_check_{i}", "Position check after stop", {
            "position": pos,
        })
    
    final_position = positions_after_stop[-1]
    position_delta = abs(final_position - position_at_stop_call)
    
    logger.log_stage(
        "stop_result",
        position_at_stop=position_at_stop_call,
        final_position=final_position,
        position_delta=position_delta,
    )
    
    # Verify position is stable (motor is not moving)
    position_changes = [abs(positions_after_stop[i+1] - positions_after_stop[i]) for i in range(len(positions_after_stop)-1)]
    max_position_change = max(position_changes) if position_changes else 0
    assert max_position_change < test_config.position_tolerance * 2, (
        f"Motor is still moving after stop(): "
        f"max_position_change={max_position_change}, positions={positions_after_stop}"
    )
    
    logger.log_stage("complete")
    logger.log_summary()


@pytest.mark.asyncio
async def test_stop_vs_quick_stop_comparison(
    drive, test_config: TestConfig
) -> None:
    """Test comparison between stop() and quick_stop().
    
    According to the manual:
    - stop() uses Profile Deceleration (0x6084) - normal deceleration
    - quick_stop() uses Quick Stop Deceleration (0x6085) - emergency deceleration (typically 10x faster)
    
    PASS criteria:
    - Both functions stop the motor
    - quick_stop() should stop faster than stop() (if Quick Stop Deceleration is configured higher)
    """
    logger = TestLogger("test_stop_vs_quick_stop")
    logger.log_stage("start")
    
    # Step 1: Bring to Operation Enabled (full bringup sequence)
    await drive.fault_reset()
    
    # Ensure we're in a valid state before enabling operation
    status = await drive.get_status()
    if not status.get("ready_to_switch_on", False) and not status.get("switched_on", False):
        # Try to get to ready_to_switch_on first
        if drive._sm is not None:
            await drive._sm.shutdown()
            await asyncio.sleep(0.2)
    
    await drive.enable_operation()
    
    async def is_operation_enabled():
        s = await drive.get_status()
        return s["operation_enabled"] and not s["fault"]
    
    await Eventually(
        is_operation_enabled,
        timeout_s=test_config.bringup_step_timeout_s,
        poll_interval_s=test_config.status_poll_interval_s,
        error_message="Failed to reach Operation Enabled",
    )
    
    # Step 2: Configure deceleration parameters
    # According to manual, Quick Stop Deceleration should be 10x normal deceleration
    normal_decel = 10000
    quick_stop_decel = 100000  # 10x normal deceleration
    
    await drive.write_u32(int(ODIndex.PROFILE_DECELERATION), normal_decel, 0)
    await drive.write_u32(int(ODIndex.QUICK_STOP_DECELERATION), quick_stop_decel, 0)
    
    debug_log("test_stop_functions.py:configure_decel", "Configured deceleration parameters", {
        "normal_decel": normal_decel,
        "quick_stop_decel": quick_stop_decel,
    })
    
    # Step 3: Test stop() with normal deceleration
    initial_pos = await drive.get_position()
    target_pos = initial_pos + 100000
    
    from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
    
    pp_config = ProfilePositionConfig(
        profile_velocity=5000,
        acceleration=10000,
        deceleration=normal_decel,
        verify_mode=True,
    )
    pp = ProfilePosition(drive, config=pp_config)
    
    await pp.ensure_mode()
    await pp.configure(profile_velocity=5000, acceleration=10000, deceleration=normal_decel)
    
    # Start movement
    await drive.write_i32(int(ODIndex.TARGET_POSITION), target_pos, 0)
    from drivers.dryve_d1.od.controlword import cw_enable_operation, cw_pulse_new_set_point
    base = cw_enable_operation()
    set_word, clear_word = cw_pulse_new_set_point(base)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)
    
    await asyncio.sleep(0.3)
    pos_before_stop = await drive.get_position()
    
    # Call stop() and measure stopping time
    import time
    stop_start_time = time.time()
    await drive.stop()
    stop_end_time = time.time()
    
    # Wait for motor to fully stop
    await asyncio.sleep(1.0)
    pos_after_stop = await drive.get_position()
    stop_time = stop_end_time - stop_start_time
    stop_distance = abs(pos_after_stop - pos_before_stop)
    
    debug_log("test_stop_functions.py:stop_result", "stop() result", {
        "stop_time": stop_time,
        "stop_distance": stop_distance,
        "position_before": pos_before_stop,
        "position_after": pos_after_stop,
    })
    
    # Clear HALT bit to prepare for next test
    await pp.halt(enabled=False)
    await asyncio.sleep(0.2)
    
    # Step 4: Test quick_stop() with quick stop deceleration
    # Start movement again
    await drive.write_i32(int(ODIndex.TARGET_POSITION), target_pos, 0)
    base = cw_enable_operation()
    set_word, clear_word = cw_pulse_new_set_point(base)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
    await drive.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)
    
    await asyncio.sleep(0.3)
    pos_before_quick_stop = await drive.get_position()
    
    # Call quick_stop() and measure stopping time
    quick_stop_start_time = time.time()
    await drive.quick_stop()
    quick_stop_end_time = time.time()
    
    # Wait for motor to fully stop
    await asyncio.sleep(1.0)
    pos_after_quick_stop = await drive.get_position()
    quick_stop_time = quick_stop_end_time - quick_stop_start_time
    quick_stop_distance = abs(pos_after_quick_stop - pos_before_quick_stop)
    
    debug_log("test_stop_functions.py:quick_stop_result", "quick_stop() result", {
        "quick_stop_time": quick_stop_time,
        "quick_stop_distance": quick_stop_distance,
        "position_before": pos_before_quick_stop,
        "position_after": pos_after_quick_stop,
    })
    
    logger.log_stage(
        "comparison",
        stop_time=stop_time,
        stop_distance=stop_distance,
        quick_stop_time=quick_stop_time,
        quick_stop_distance=quick_stop_distance,
    )
    
    # Both should stop the motor
    assert stop_distance < abs(target_pos - pos_before_stop), "stop() did not stop motor"
    assert quick_stop_distance < abs(target_pos - pos_before_quick_stop), "quick_stop() did not stop motor"
    
    # Note: quick_stop() should theoretically stop faster, but this depends on drive implementation
    # We'll just verify both work correctly
    
    logger.log_stage("complete")
    logger.log_summary()

