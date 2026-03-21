"""Tests for graceful shutdown while navigation tasks are active.

Covers:
- StatusPublisher.stop() cancels transport watcher tasks
- StatusPublisher.stop() completes within timeout even with running tasks
- AppServices.stop() stops services in correct order
- Shutdown does not raise even if individual services fail
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.status_publisher import StatusPublisher
from services.event_bus import EventBus
from services.state_store import StateStore
from app.container import AppServices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_publisher() -> StatusPublisher:
    """Create a StatusPublisher with fully mocked dependencies."""
    symovo = MagicMock()
    symovo.close = AsyncMock()
    bus = EventBus(queue_size=10)
    store = MagicMock(spec=StateStore)
    store.get_active_commands_for_publishing = AsyncMock(return_value={})
    store.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, None))
    return StatusPublisher(symovo_client=symovo, bus=bus, state_store=store)


def _make_app_services(**overrides) -> AppServices:
    """Build an AppServices with all-mock fields."""
    defaults = dict(
        symovo_client=MagicMock(close=AsyncMock()),
        command_handler=MagicMock(),
        status_publisher=MagicMock(stop=AsyncMock()),
        event_dispatcher=MagicMock(stop=AsyncMock()),
        event_stream=MagicMock(stop=AsyncMock()),
        event_bus=MagicMock(),
        state_store=MagicMock(),
        bg_tasks=[],
    )
    defaults.update(overrides)
    return AppServices(**defaults)


# ---------------------------------------------------------------------------
# StatusPublisher.stop() — transport watcher cancellation
# ---------------------------------------------------------------------------

class TestStatusPublisherShutdown:

    @pytest.mark.asyncio
    async def test_stop_cancels_transport_watcher_tasks(self):
        """Transport watcher tasks stored in _transport_tasks must be cancelled."""
        pub = _make_publisher()
        pub._running_flag.set()

        # Simulate two transport watcher tasks
        async def _hang():
            await asyncio.sleep(300)

        t1 = asyncio.create_task(_hang())
        t2 = asyncio.create_task(_hang())
        pub._transport_tasks = {
            "cmd-1": ("tr-1", t1),
            "cmd-2": ("tr-2", t2),
        }
        # Also add a main task
        t3 = asyncio.create_task(_hang())
        pub._tasks = [t3]

        await pub.stop()

        assert t1.cancelled() or t1.done()
        assert t2.cancelled() or t2.done()
        assert t3.cancelled() or t3.done()
        assert pub._transport_tasks == {}
        assert pub._tasks == []

    @pytest.mark.asyncio
    async def test_stop_completes_within_timeout(self):
        """stop() must finish even if tasks take a while to cancel."""
        pub = _make_publisher()
        pub._running_flag.set()

        async def _stubborn():
            try:
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                # Simulate slow cleanup
                await asyncio.sleep(0.05)
                raise

        t = asyncio.create_task(_stubborn())
        pub._tasks = [t]

        # Should complete well within the 5-second internal timeout
        await asyncio.wait_for(pub.stop(), timeout=6.0)
        assert not pub.is_running

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self):
        pub = _make_publisher()
        pub._running_flag.set()
        assert pub.is_running

        await pub.stop()
        assert not pub.is_running

    @pytest.mark.asyncio
    async def test_stop_is_safe_with_no_tasks(self):
        """Calling stop() before start() must not raise."""
        pub = _make_publisher()
        await pub.stop()  # no tasks to cancel
        assert not pub.is_running

    @pytest.mark.asyncio
    async def test_stop_handles_already_done_tasks(self):
        """Tasks that finished before stop() should not cause errors."""
        pub = _make_publisher()
        pub._running_flag.set()

        async def _instant():
            return

        t = asyncio.create_task(_instant())
        await t  # let it finish
        pub._tasks = [t]

        await pub.stop()  # must not raise
        assert pub._tasks == []


# ---------------------------------------------------------------------------
# AppServices.stop() — ordering and resilience
# ---------------------------------------------------------------------------

class TestAppServicesShutdown:

    @pytest.mark.asyncio
    async def test_stop_calls_services_in_order(self):
        """Services must stop in the documented order:
        bg_tasks -> event_stream -> status_publisher -> event_dispatcher -> symovo_client.
        """
        call_order: list[str] = []

        def _make_recorder(name: str):
            async def _side_effect(*args, **kwargs):
                call_order.append(name)
            return _side_effect

        svc = _make_app_services()
        svc.event_stream.stop = AsyncMock(side_effect=_make_recorder("event_stream"))
        svc.status_publisher.stop = AsyncMock(side_effect=_make_recorder("status_publisher"))
        svc.event_dispatcher.stop = AsyncMock(side_effect=_make_recorder("event_dispatcher"))
        svc.symovo_client.close = AsyncMock(side_effect=_make_recorder("symovo_client"))

        await svc.stop()

        assert call_order == [
            "event_stream",
            "status_publisher",
            "event_dispatcher",
            "symovo_client",
        ]

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        """Calling stop() twice must not raise or re-run teardown."""
        svc = _make_app_services()

        await svc.stop()
        await svc.stop()

        # status_publisher.stop() should only have been called once
        svc.status_publisher.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_continues_after_event_stream_failure(self):
        """If event_stream.stop() raises, the remaining services must still stop."""
        svc = _make_app_services()
        svc.event_stream.stop = AsyncMock(side_effect=RuntimeError("boom"))

        await svc.stop()

        svc.status_publisher.stop.assert_awaited_once()
        svc.event_dispatcher.stop.assert_awaited_once()
        svc.symovo_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_continues_after_publisher_failure(self):
        """If status_publisher.stop() raises, dispatcher and symovo still stop."""
        svc = _make_app_services()
        svc.status_publisher.stop = AsyncMock(side_effect=RuntimeError("pub crash"))

        await svc.stop()

        svc.event_dispatcher.stop.assert_awaited_once()
        svc.symovo_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_tasks(self):
        """Background tasks owned by the container are cancelled during stop."""
        async def _hang():
            await asyncio.sleep(300)

        t = asyncio.create_task(_hang())
        svc = _make_app_services(bg_tasks=[t])

        await svc.stop()

        assert t.cancelled() or t.done()

    @pytest.mark.asyncio
    async def test_stop_handles_bg_task_exception(self):
        """A background task that raises on cancel must not block shutdown."""
        async def _bad_task():
            try:
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                raise RuntimeError("cleanup failed")

        t = asyncio.create_task(_bad_task())
        svc = _make_app_services(bg_tasks=[t])

        # Must not raise
        await svc.stop()
