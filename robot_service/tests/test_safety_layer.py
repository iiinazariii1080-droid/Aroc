import asyncio
import time
import pytest

import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.xarm_websocket_client.safety_layer import SafetyLayer


@pytest.mark.asyncio
async def test_safety_layer_heartbeat():
    """Test that heartbeat() updates last_msg_ts"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=1.0,
        hold_timeout=0.5
    )

    try:
        initial_ts = safety.last_msg_ts
        await asyncio.sleep(0.1)  # Small delay

        # Call heartbeat
        safety.heartbeat()
        heartbeat_ts = safety.last_msg_ts

        # Timestamp should be updated
        assert heartbeat_ts > initial_ts, "Heartbeat should update last_msg_ts"

        # Should be very recent
        assert time.time() - heartbeat_ts < 0.01, "Heartbeat timestamp should be current"

    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_layer_watchdog_no_heartbeat():
    """Test that watchdog triggers stop when no messages and no heartbeat"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=0.5,  # Short timeout for test
        hold_timeout=0.5
    )

    try:
        # Wait longer than watchdog timeout
        await asyncio.sleep(0.7)

        # Should have triggered watchdog timeout
        assert len(stop_calls) > 0, "Watchdog should trigger stop when no activity"
        assert "watchdog_timeout" in stop_calls, f"Expected watchdog_timeout, got: {stop_calls}"

    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_layer_watchdog_with_heartbeat():
    """Test that watchdog does not trigger when heartbeat is called regularly"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=0.5,  # Short timeout for test
        hold_timeout=0.5
    )

    try:
        # Send heartbeat very frequently to prevent watchdog
        for i in range(10):
            safety.heartbeat()
            await asyncio.sleep(0.1)  # Much less than watchdog timeout

        # Should not have triggered watchdog timeout
        assert len(stop_calls) == 0, f"Watchdog should not trigger with heartbeat, but got stops: {stop_calls}"

    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_layer_watchdog_with_messages():
    """Test that watchdog does not trigger when messages are received"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=0.5,  # Short timeout for test
        hold_timeout=0.5
    )

    try:
        # Send messages frequently
        for i in range(10):
            test_msg = {"type": "report", "cmd": "status", "data": {"ts": time.time()}}
            await safety.handle_message(test_msg)
            await asyncio.sleep(0.1)  # Much less than watchdog timeout

        # Should not have triggered watchdog timeout
        assert len(stop_calls) == 0, f"Watchdog should not trigger with messages, but got stops: {stop_calls}"

    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_layer_hold_timeout():
    """Test hold timeout functionality"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=2.0,  # Long timeout so only hold timeout triggers
        hold_timeout=0.3  # Short hold timeout for test
    )

    try:
        # Send move_step_over message to start hold timer
        move_over_msg = {"type": "request", "cmd": "xarm_move_step_over", "data": {"ts": time.time()}}
        await safety.handle_message(move_over_msg)

        # Wait longer than hold timeout
        await asyncio.sleep(0.5)

        # Should have triggered deadman release
        assert len(stop_calls) > 0, "Hold timeout should trigger stop"
        assert "deadman_release" in stop_calls, f"Expected deadman_release, got: {stop_calls}"

    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_layer_expired_command():
    """Test TTL check for expired commands"""
    stop_calls = []
    forwarded_messages = []

    async def fake_send_stop(reason: str):
        stop_calls.append(reason)

    async def fake_forward_command(msg: dict):
        forwarded_messages.append(msg)

    safety = SafetyLayer(
        send_stop=fake_send_stop,
        forward_command=fake_forward_command,
        watchdog_timeout=2.0,
        hold_timeout=0.5
    )

    try:
        # Send message with expired timestamp
        expired_msg = {
            "type": "request",
            "cmd": "xarm_move_step",
            "data": {
                "ts": time.time() - 10,  # 10 seconds ago
                "ttl": 1.0  # 1 second TTL
            }
        }
        await safety.handle_message(expired_msg)

        # Should have triggered expired_command stop
        assert len(stop_calls) > 0, "Expired command should trigger stop"
        assert "expired_command" in stop_calls, f"Expected expired_command, got: {stop_calls}"

        # Should not forward expired message
        assert len(forwarded_messages) == 0, "Expired message should not be forwarded"

    finally:
        await safety.stop()
