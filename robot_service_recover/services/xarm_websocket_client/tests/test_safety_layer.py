import asyncio
import time
import pytest

from safety_layer import SafetyLayer


@pytest.mark.asyncio
async def test_safety_ttl_ignored_for_list_data_and_forwarded():
    stops = []
    forwarded = []

    async def send_stop(reason: str):
        stops.append(reason)

    async def forward_command(msg):
        forwarded.append(msg)

    safety = SafetyLayer(send_stop=send_stop, forward_command=forward_command, watchdog_timeout=0.2, hold_timeout=0.2)
    try:
        # list data (status report) should not trigger TTL check and should be forwarded
        msg = {"type": "report", "cmd": "devices_status_report", "data": [1, 2, 3]}
        await safety.handle_message(msg)

        assert len(stops) == 0
        assert forwarded and forwarded[-1]["cmd"] == "devices_status_report"
    finally:
        await safety.stop()


@pytest.mark.asyncio
async def test_safety_deadman_sets_last_hold():
    stops = []
    forwarded = []

    async def send_stop(reason: str):
        stops.append(reason)

    async def forward_command(msg):
        forwarded.append(msg)

    safety = SafetyLayer(send_stop=send_stop, forward_command=forward_command, watchdog_timeout=0.2, hold_timeout=0.1)
    try:
        msg = {"type": "report", "cmd": "xarm_move_step_over", "data": {}}
        await safety.handle_message(msg)

        # allow watchdog to elapse for hold timeout
        await asyncio.sleep(0.15)
        # one iteration of watchdog loop should have triggered
        assert "deadman_release" in stops
    finally:
        await safety.stop()


