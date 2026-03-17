"""Tests for concurrent SSE subscribers (TEST-03).

Covers: multiple subscribers, subscriber drop + rejoin, ring buffer replay
during high publish rate.
"""

import asyncio

import pytest

from app.events import DROPPED, EventBus, EventType


class TestConcurrentSSESubscribers:
    """EventBus behavior under multiple concurrent subscribers."""

    def test_multiple_subscribers_receive_same_event(self):
        """All active subscribers receive every published event."""
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(10)]

        bus.publish(EventType.STATUS, {"position": 100})

        for q in queues:
            assert not q.empty()
            event = q.get_nowait()
            assert event.type == EventType.STATUS
            assert event.payload["position"] == 100

    def test_slow_subscriber_dropped(self):
        """A subscriber whose queue is full receives a DROPPED sentinel."""
        bus = EventBus()
        fast_q = bus.subscribe()
        slow_q = bus.subscribe()

        # Fill the slow subscriber's queue (maxsize=100)
        for i in range(101):
            bus.publish(EventType.STATUS, {"i": i})

        # Fast subscriber should have all events
        assert not fast_q.empty()

        # Slow subscriber should have been dropped
        assert bus.subscribers_dropped_total >= 1

        # Drain slow_q — it should contain a DROPPED sentinel
        found_dropped = False
        while not slow_q.empty():
            item = slow_q.get_nowait()
            if item is DROPPED:
                found_dropped = True
                break
        assert found_dropped

    def test_subscriber_dropped_and_new_joins(self):
        """After a subscriber is dropped, a new subscriber can join and receive events."""
        bus = EventBus()
        old_q = bus.subscribe()

        # Fill and drop old subscriber
        for i in range(110):
            bus.publish(EventType.STATUS, {"i": i})

        # Old subscriber removed from active list
        assert bus.subscriber_count == 0

        # New subscriber joins and receives new events
        new_q = bus.subscribe()
        assert bus.subscriber_count == 1

        bus.publish(EventType.FAULT, {"active": True})

        event = new_q.get_nowait()
        assert event.type == EventType.FAULT
        assert event.payload["active"] is True

    def test_ring_buffer_replay_during_high_publish_rate(self):
        """Ring buffer provides recent events for late-joining subscribers."""
        bus = EventBus(recent_buffer_size=50)

        # Publish 200 events
        for i in range(200):
            bus.publish(EventType.STATUS, {"seq": i})

        # Late joiner gets last 20 events
        recent = bus.get_recent_events(limit=20)
        assert len(recent) == 20
        assert recent[0].payload["seq"] == 180
        assert recent[-1].payload["seq"] == 199

    def test_hundred_concurrent_subscribers(self):
        """100 subscribers all receive the same event without errors."""
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(100)]

        assert bus.subscriber_count == 100

        bus.publish(EventType.STATE_CHANGE, {"from_state": "A", "to_state": "B"})

        for q in queues:
            event = q.get_nowait()
            assert event.type == EventType.STATE_CHANGE

    def test_unsubscribe_reduces_count(self):
        """Unsubscribing removes the subscriber."""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert bus.subscriber_count == 2

        bus.unsubscribe(q1)
        assert bus.subscriber_count == 1

        bus.publish(EventType.STATUS, {"x": 1})
        assert q2.get_nowait().payload["x"] == 1
        assert q1.empty()

    def test_shutdown_notify_clears_subscribers(self):
        """shutdown_notify publishes SHUTDOWN and clears all subscribers."""
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(5)]

        bus.shutdown_notify()

        assert bus.subscriber_count == 0
        for q in queues:
            event = q.get_nowait()
            assert event.type == EventType.SHUTDOWN
