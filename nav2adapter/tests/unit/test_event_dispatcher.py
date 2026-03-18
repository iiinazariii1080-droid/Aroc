"""Tests for services/event_dispatcher.py — start/stop, event processing, persistence."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.event_dispatcher import EventDispatcher
from services.event_bus import EventBus
from domain.events import AckEvent, ResultSuccessEvent, StateProgressEvent


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus)
        await d.start()
        assert d._running is True
        assert d._task is not None
        await d.stop()
        assert d._running is False
        assert d._task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus)
        await d.start()
        task1 = d._task
        await d.start()  # no-op
        assert d._task is task1
        await d.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus)
        await d.stop()  # no-op, should not crash


class TestEventProcessing:
    @pytest.mark.asyncio
    async def test_result_event_persists(self):
        bus = EventBus(queue_size=10)
        ss = MagicMock()
        ss.set_last_result = AsyncMock()
        d = EventDispatcher(bus, state_store=ss)
        await d.start()
        await asyncio.sleep(0.05)  # let task subscribe
        try:
            event = ResultSuccessEvent(type="result.success", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            ss.set_last_result.assert_called_once()
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_ack_event_does_not_persist(self):
        """Non-result events should not be persisted."""
        bus = EventBus(queue_size=10)
        ss = MagicMock()
        ss.set_last_result = AsyncMock()
        d = EventDispatcher(bus, state_store=ss)
        await d.start()
        await asyncio.sleep(0.05)
        try:
            event = AckEvent(type="ack.received", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            ss.set_last_result.assert_not_called()
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_no_state_store(self):
        """Dispatcher works without state_store."""
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus)
        await d.start()
        await asyncio.sleep(0.05)
        try:
            event = ResultSuccessEvent(type="result.success", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            # Should process without crash
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_error_in_persist_does_not_crash(self):
        bus = EventBus(queue_size=10)
        ss = MagicMock()
        ss.set_last_result = AsyncMock(side_effect=RuntimeError("boom"))
        d = EventDispatcher(bus, state_store=ss)
        await d.start()
        await asyncio.sleep(0.05)
        try:
            event = ResultSuccessEvent(type="result.success", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            # Should still be running
            assert d._running is True
        finally:
            await d.stop()
