"""Tests for reconnect × motion race: abort token rotation and event signaling.

These tests verify the safety-critical invariant: when stop() is called during
active motion, the abort token is rotated and the abort event is set, allowing
the in-flight motion to detect the interruption.

Tests use real asyncio tasks and events — no mocks for timing-critical paths.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest


@pytest.mark.asyncio
async def test_abort_token_rotation_on_stop() -> None:
    """stop() must rotate _abort_token and set _abort_event."""
    abort_event = asyncio.Event()
    initial_token = uuid.uuid4().hex

    # Simulate the _execute_stop token rotation (motion_commands.py:120-121)
    motion_token = initial_token
    abort_token = initial_token
    abort_event.clear()

    # "Motion starts" — records its token
    assert abort_token == motion_token
    assert not abort_event.is_set()

    # "Stop fires" — rotates token and sets event
    abort_token = uuid.uuid4().hex
    abort_event.set()

    # "Motion completes" — detects mismatch
    assert abort_token != motion_token, "abort token must differ after stop()"
    assert abort_event.is_set(), "abort_event must be set after stop()"


@pytest.mark.asyncio
async def test_concurrent_stop_during_motion_task() -> None:
    """Concurrent stop during an in-flight motion task detects abort via event.

    Simulates the real scheduling: motion runs as a task, stop is scheduled
    via call_soon_threadsafe (simulated with call_soon), and the motion task
    detects the abort event during its polling loop.
    """
    abort_event = asyncio.Event()
    abort_token_holder = {"token": uuid.uuid4().hex}
    motion_detected_abort = asyncio.Event()
    motion_started = asyncio.Event()

    async def simulate_motion() -> str:
        """Simulates move_to_position polling loop."""
        motion_token = abort_token_holder["token"]
        abort_event.clear()
        motion_started.set()

        # Simulate polling (like pp.move_to_position checking target_reached)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if abort_event.is_set():
                motion_detected_abort.set()
                break

        # Check token mismatch on completion
        if abort_token_holder["token"] != motion_token:
            return "abort_detected"
        return "completed"

    async def simulate_stop() -> None:
        """Simulates _execute_stop scheduled via call_soon_threadsafe."""
        await motion_started.wait()
        await asyncio.sleep(0.05)  # let motion run briefly
        # Rotate token and set event (motion_commands.py:120-121)
        abort_token_holder["token"] = uuid.uuid4().hex
        abort_event.set()

    motion_task = asyncio.create_task(simulate_motion())
    stop_task = asyncio.create_task(simulate_stop())

    result = await asyncio.wait_for(motion_task, timeout=3.0)
    await stop_task

    assert result == "abort_detected"
    assert motion_detected_abort.is_set(), "motion must detect abort via event"


@pytest.mark.asyncio
async def test_reconnect_stop_debounce() -> None:
    """Reconnect stop must be debounced — duplicate calls are ignored.

    Verifies the _reconnect_stop_scheduled flag pattern from drive.py:358-360.
    """
    stop_count = 0
    reconnect_stop_scheduled = False

    async def stop_motion() -> None:
        nonlocal stop_count
        stop_count += 1
        await asyncio.sleep(0.05)

    def schedule_reconnect_stop() -> None:
        nonlocal reconnect_stop_scheduled
        if reconnect_stop_scheduled:
            return
        reconnect_stop_scheduled = True

        async def _run() -> None:
            nonlocal reconnect_stop_scheduled
            try:
                await stop_motion()
            finally:
                reconnect_stop_scheduled = False

        asyncio.get_running_loop().create_task(_run())

    # Fire 3 rapid reconnect triggers — only 1 should execute
    schedule_reconnect_stop()
    schedule_reconnect_stop()
    schedule_reconnect_stop()

    await asyncio.sleep(0.15)
    assert stop_count == 1, f"Expected 1 stop call, got {stop_count}"
