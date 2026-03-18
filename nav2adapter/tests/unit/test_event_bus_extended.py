"""Extended tests for services/event_bus.py — overflow, stream, drain."""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from services.event_bus import EventBus


def _make_event(**kw):
    ev = MagicMock()
    ev.type = kw.get("type", "ack.received")
    ev.model_dump.return_value = {"type": ev.type}
    return ev


class TestPublishOverflow:
    @pytest.mark.asyncio
    async def test_drop_oldest_on_overflow(self):
        bus = EventBus(queue_size=1)
        q = await bus.subscribe()
        e1 = _make_event(type="first")
        e2 = _make_event(type="second")
        await bus.publish(e1)
        await bus.publish(e2)
        # Should have dropped e1 and kept e2
        item = q.get_nowait()
        assert item.type == "second"

    @pytest.mark.asyncio
    async def test_delivery_failed_on_broken_subscriber(self):
        bus = EventBus(queue_size=2)
        q = await bus.subscribe()
        # Deliberately break the queue
        q.put_nowait = MagicMock(side_effect=RuntimeError("broken"))
        q.full = MagicMock(return_value=False)
        await bus.publish(_make_event())
        # Should not raise — just silently fail

    @pytest.mark.asyncio
    async def test_drop_rate_limited_logging(self):
        bus = EventBus(queue_size=1)
        q = await bus.subscribe()
        bus._last_drop_log_time = {}  # per-subscriber dict
        await bus.publish(_make_event())
        await bus.publish(_make_event())  # triggers drop
        # The warning should have been issued (per-subscriber tracking)
        assert len(bus._last_drop_log_time) > 0


class TestDrain:
    @pytest.mark.asyncio
    async def test_drain_empty(self):
        bus = EventBus()
        q = await bus.subscribe()
        items = await bus.drain(q)
        assert items == []

    @pytest.mark.asyncio
    async def test_drain_max_items(self):
        bus = EventBus(queue_size=50)
        q = await bus.subscribe()
        for i in range(20):
            await bus.publish(_make_event())
        items = await bus.drain(q, max_items=5)
        assert len(items) == 5
        # 15 should remain
        remaining = await bus.drain(q)
        assert len(remaining) == 15


class TestSubscribeUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self):
        bus = EventBus()
        q = await bus.subscribe()
        assert q in bus._subscribers
        await bus.unsubscribe(q)
        assert q not in bus._subscribers

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self):
        bus = EventBus()
        q = await bus.subscribe()
        await bus.unsubscribe(q)
        await bus.unsubscribe(q)  # No error
