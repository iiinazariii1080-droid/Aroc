"""Tests for EventBus: ring-buffer replay and DROPPED sentinel behaviour."""
from __future__ import annotations

import asyncio

import pytest

from app.events import DROPPED, DriveEvent, EventBus, EventType, _DroppedSentinel


# ---------------------------------------------------------------------------
# get_recent_events() — ring-buffer replay
# ---------------------------------------------------------------------------


async def test_recent_events_empty_on_fresh_bus() -> None:
    bus = EventBus()
    assert bus.get_recent_events() == []


async def test_recent_events_returns_published_events() -> None:
    bus = EventBus()
    bus.publish(EventType.STATUS, {"a": 1})
    bus.publish(EventType.STATUS, {"b": 2})

    events = bus.get_recent_events()
    assert len(events) == 2
    assert events[0].payload == {"a": 1}
    assert events[1].payload == {"b": 2}


async def test_recent_events_limit_trims_oldest() -> None:
    bus = EventBus()
    for i in range(5):
        bus.publish(EventType.STATUS, {"i": i})

    events = bus.get_recent_events(limit=3)
    assert len(events) == 3
    # Should be the 3 most recent
    assert [e.payload["i"] for e in events] == [2, 3, 4]


async def test_recent_events_limit_zero_returns_empty() -> None:
    bus = EventBus()
    bus.publish(EventType.STATUS, {"x": 0})
    assert bus.get_recent_events(limit=0) == []


async def test_recent_events_limit_negative_returns_empty() -> None:
    bus = EventBus()
    bus.publish(EventType.STATUS, {"x": 0})
    assert bus.get_recent_events(limit=-1) == []


async def test_recent_events_ring_buffer_drops_oldest_when_full() -> None:
    buffer_size = 5
    bus = EventBus(recent_buffer_size=buffer_size)
    for i in range(buffer_size + 3):
        bus.publish(EventType.STATUS, {"i": i})

    events = bus.get_recent_events()
    assert len(events) == buffer_size
    # Oldest 3 were evicted; most recent 5 remain
    assert events[0].payload["i"] == 3
    assert events[-1].payload["i"] == buffer_size + 2


async def test_recent_events_returns_drive_event_instances() -> None:
    bus = EventBus()
    bus.publish(EventType.COMMAND, {"op": "move"})
    events = bus.get_recent_events()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, DriveEvent)
    assert ev.type == EventType.COMMAND
    assert ev.seq == 1


# ---------------------------------------------------------------------------
# DROPPED sentinel
# ---------------------------------------------------------------------------


def test_dropped_is_singleton() -> None:
    assert isinstance(DROPPED, _DroppedSentinel)


async def test_subscriber_removed_when_queue_full() -> None:
    """publish() removes a subscriber when its queue is full.

    After put_nowait(event) raises QueueFull, the queue is still at maxsize,
    so put_nowait(DROPPED) also raises QueueFull (suppressed).  The important
    invariant is that the subscriber is removed regardless of whether DROPPED
    could be placed.
    """
    bus = EventBus()
    q = bus.subscribe()
    # Fill the queue to capacity so the next publish is rejected
    for _ in range(q.maxsize):
        q.put_nowait(object())

    bus.publish(EventType.STATUS, {"x": 1})

    # The subscriber must be removed from the bus
    assert len(bus._subscribers) == 0


async def test_slow_subscriber_removed_on_full_queue() -> None:
    """A subscriber whose queue overflows is evicted from the bus."""
    bus = EventBus()
    good_q = bus.subscribe()
    slow_q = bus.subscribe()

    # Fill slow_q to capacity
    for _ in range(slow_q.maxsize):
        slow_q.put_nowait(object())

    assert len(bus._subscribers) == 2

    bus.publish(EventType.STATUS, {"evict": True})

    # slow_q evicted; good_q still present and received the event
    assert len(bus._subscribers) == 1
    assert bus._subscribers[0] is good_q
    assert not good_q.empty()
    ev = good_q.get_nowait()
    assert isinstance(ev, DriveEvent)


async def test_unsubscribe_removes_subscriber() -> None:
    bus = EventBus()
    q = bus.subscribe()
    assert len(bus._subscribers) == 1

    bus.unsubscribe(q)
    assert len(bus._subscribers) == 0

    # unsubscribe of an already-removed queue must not raise
    bus.unsubscribe(q)


async def test_unsubscribe_does_not_affect_other_subscribers() -> None:
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()

    bus.unsubscribe(q1)

    assert len(bus._subscribers) == 1
    assert bus._subscribers[0] is q2


# ---------------------------------------------------------------------------
# seq monotonicity
# ---------------------------------------------------------------------------


async def test_event_seq_monotonically_increases() -> None:
    bus = EventBus()
    for _ in range(3):
        bus.publish(EventType.STATUS, {})

    events = bus.get_recent_events()
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, 4))


# ---------------------------------------------------------------------------
# Thread-safety regression: verify publish() from event loop delivers events
# ---------------------------------------------------------------------------


async def test_publish_from_event_loop_delivers_to_subscriber() -> None:
    """Regression: publish() called from the event loop thread delivers events.

    This locks the invariant that all publish() calls happen on the event loop,
    making explicit locking unnecessary. If this invariant is broken (e.g., by
    calling publish from a background thread without call_soon_threadsafe),
    events may be lost or subscribers may be corrupted.
    """
    bus = EventBus()
    queue = bus.subscribe()

    bus.publish(EventType.STATUS, {"position": 42})

    event = queue.get_nowait()
    assert event is not DROPPED
    assert event.type == EventType.STATUS
    assert event.payload["position"] == 42

    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0


async def test_publish_delivers_to_multiple_subscribers() -> None:
    """All subscribers receive the same event from a single publish()."""
    bus = EventBus()
    queues = [bus.subscribe() for _ in range(5)]

    bus.publish(EventType.STATE_CHANGE, {"state": "fault"})

    for q in queues:
        event = q.get_nowait()
        assert event is not DROPPED
        assert event.payload["state"] == "fault"

    for q in queues:
        bus.unsubscribe(q)
    assert bus.subscriber_count == 0
