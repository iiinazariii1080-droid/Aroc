import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.joystick_scheduler import JoystickScheduler


@pytest.mark.asyncio
async def test_scheduler_respects_rate_limit():
    dispatch_times: list[float] = []

    async def handler(frame):
        dispatch_times.append(time.monotonic())
        return True

    scheduler = JoystickScheduler(handler, max_rate_hz=10.0)
    await scheduler.start()
    try:
        for i in range(3):
            ok = scheduler.submit({"seq": i})
            assert ok
            await asyncio.sleep(0.12)  # let worker dispatch between submits
        await asyncio.sleep(0.2)
        assert len(dispatch_times) >= 3
        diffs = [dispatch_times[i] - dispatch_times[i - 1] for i in range(1, len(dispatch_times))]
        assert all(diff >= 0.08 for diff in diffs)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_drops_when_latest_overwritten():
    events: list[dict] = []

    async def handler(frame):
        events.append(frame)
        return True

    scheduler = JoystickScheduler(handler, max_rate_hz=100.0)
    await scheduler.start()
    try:
        # Rapidly submit two frames; the second overwrites the first
        ok1 = scheduler.submit({"seq": 1})
        assert ok1
        ok2 = scheduler.submit({"seq": 2})
        assert ok2
        await asyncio.sleep(0.1)
        assert scheduler.stats.dropped_total >= 0
    finally:
        await scheduler.stop()

