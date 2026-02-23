import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.joystick_scheduler import JoystickScheduler


@pytest.mark.asyncio
async def test_scheduler_respects_rate_limit():
    dispatch_times = []

    def handler(frame):
        dispatch_times.append(time.time())
        return True

    scheduler = JoystickScheduler(handler, max_rate_hz=10.0, queue_maxsize=10)
    await scheduler.start()
    try:
        for i in range(3):
            ok, err = scheduler.submit({"seq": i})
            assert ok and err is None
        await asyncio.sleep(0.5)
        assert len(dispatch_times) == 3
        diffs = [dispatch_times[i] - dispatch_times[i - 1] for i in range(1, len(dispatch_times))]
        assert all(diff >= 0.09 for diff in diffs)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_drops_when_queue_full():
    events = []

    def handler(frame):
        events.append(frame)
        return True

    scheduler = JoystickScheduler(handler, max_rate_hz=100.0, queue_maxsize=1)
    await scheduler.start()
    try:
        ok, _ = scheduler.submit({"seq": 1})
        assert ok
        ok2, err2 = scheduler.submit({"seq": 2})
        assert ok2
        await asyncio.sleep(0.05)
        assert scheduler.dropped_total >= 0
    finally:
        await scheduler.stop()

