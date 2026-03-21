"""Stress tests for EventBus overflow under sustained load.

Verifies that the EventBus handles queue saturation gracefully:
- Slow subscribers don't block publishers
- Drop-oldest policy prevents memory growth
- Bus remains functional after overflow
"""
import asyncio
import pytest
from services.event_bus import EventBus


class TestEventBusOverflow:
    @pytest.mark.asyncio
    async def test_publish_does_not_block_on_full_queue(self):
        """Publishing to a full subscriber queue must not block the publisher."""
        bus = EventBus(queue_size=5)
        q = await bus.subscribe()

        # Publish more events than queue can hold
        for i in range(20):
            await bus.publish({"seq": i})

        # Publisher should not have blocked — we get here immediately
        items = await bus.drain(q, max_items=100)
        assert len(items) <= 5

    @pytest.mark.asyncio
    async def test_bus_functional_after_overflow(self):
        """After overflow, new events still reach subscribers."""
        bus = EventBus(queue_size=3)
        q = await bus.subscribe()

        # Overflow the queue
        for i in range(10):
            await bus.publish({"seq": i})

        # Drain
        await bus.drain(q, max_items=100)

        # New event should still arrive
        await bus.publish({"seq": "post-overflow"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event["seq"] == "post-overflow"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_independent_overflow(self):
        """Overflow on one subscriber doesn't affect others."""
        bus = EventBus(queue_size=3)
        fast_q = await bus.subscribe()
        slow_q = await bus.subscribe()

        # Fast consumer drains immediately after each publish
        for i in range(10):
            await bus.publish({"seq": i})
            await bus.drain(fast_q, max_items=100)

        # Slow consumer overflowed — should have at most queue_size items
        slow_items = await bus.drain(slow_q, max_items=100)
        assert len(slow_items) <= 3

        # Both still work
        await bus.publish({"seq": "final"})
        fast_event = await asyncio.wait_for(fast_q.get(), timeout=1.0)
        slow_event = await asyncio.wait_for(slow_q.get(), timeout=1.0)
        assert fast_event["seq"] == "final"
        assert slow_event["seq"] == "final"

    @pytest.mark.asyncio
    async def test_sustained_load_no_memory_leak(self):
        """Sustained publishing with no consumer doesn't grow memory."""
        bus = EventBus(queue_size=10)
        q = await bus.subscribe()

        # Publish 1000 events without consuming
        for i in range(1000):
            await bus.publish({"seq": i})

        # Queue should be bounded
        items = await bus.drain(q, max_items=2000)
        assert len(items) <= 10

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        """After unsubscribe, events are no longer queued."""
        bus = EventBus(queue_size=10)
        q = await bus.subscribe()

        await bus.publish({"before": True})
        assert not q.empty()

        await bus.unsubscribe(q)
        await bus.publish({"after": True})

        # Drain — should only have the "before" event
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(e.get("before") for e in items)
        assert not any(e.get("after") for e in items)
