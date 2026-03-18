"""Integration-ish tests for StatusPublisher transport watcher management.

These tests are designed to reproduce the 'ID confusion' class of bugs:
- transport_id reuse across different command_id values must not suppress new watchers
- stop() must await transport watchers so they cannot continue publishing after shutdown
"""

import asyncio
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.status_publisher import StatusPublisher
from domain.models import ActiveTransport


@pytest.mark.asyncio
async def test_transport_manager_creates_watcher_per_command_even_if_transport_id_reused(
    mock_symovo_client, event_bus_instance
):
    ss = MagicMock()
    ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
    ss.set_last_navigation_status = AsyncMock()
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        bus=event_bus_instance, state_store=ss,
    )

    seen = []

    shared_transport_id = "transport_SHARED"
    t1 = ActiveTransport(
        command_id="cmd_A",
        transport_id=shared_transport_id,
        state=1,
        created_at=datetime.now(timezone.utc),
        target_id="position_A",
        generation=0,
    )
    t2 = ActiveTransport(
        command_id="cmd_B",
        transport_id=shared_transport_id,
        state=1,
        created_at=datetime.now(timezone.utc),
        target_id="position_B",
        generation=0,
    )

    call_n = 0

    async def fake_get_active_commands():
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            return {"cmd_A": t1}
        if call_n <= 4:
            return {"cmd_B": t2}
        publisher._running_flag.clear()
        return {}

    ss.get_active_commands_for_publishing = fake_get_active_commands

    # Patch TransportWatcherTask to record what watchers were created
    class FakeWatcher:
        def __init__(self, **kwargs):
            pass
        async def run(self, *, command_id, transport_id):
            seen.append((command_id, transport_id))
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

    with patch("services.status_publisher.TransportWatcherTask", FakeWatcher):
        publisher._running_flag.set()
        tm_task = asyncio.create_task(publisher._transport_manager())
        await tm_task

    # With old bug (keyed by transport_id), only first watcher would be created.
    assert ("cmd_A", shared_transport_id) in seen
    assert ("cmd_B", shared_transport_id) in seen
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_stop_awaits_transport_watchers(mock_symovo_client, event_bus_instance, state_store):
    publisher = StatusPublisher(
        symovo_client=mock_symovo_client,
        bus=event_bus_instance,
        state_store=state_store,
    )

    # Create a watcher that delays cancellation handling to make the test deterministic.
    async def slow_cancel_watcher():
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # simulate cleanup delay
            await asyncio.sleep(0.2)
            raise

    t = asyncio.create_task(slow_cancel_watcher())
    publisher._transport_tasks["cmd_A"] = ("transport_1", t)

    # Also add a main task so stop() covers both lists
    async def main_task():
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)
            raise

    m = asyncio.create_task(main_task())
    publisher._tasks.append(m)

    # stop must await both tasks to completion (within timeout)
    await publisher.stop()

    assert t.done(), "transport watcher must be awaited and completed during stop()"
    assert m.done(), "main task must be awaited and completed during stop()"
