"""Event bus for pub/sub and SSE events."""

import asyncio
import contextlib
import copy
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Event type."""
    STATUS = "status"
    STATE_CHANGE = "state_change"
    FAULT = "fault"
    COMMAND = "command"
    SHUTDOWN = "shutdown"


@dataclass
class DriveEvent:
    """Drive event model."""
    seq: int
    ts: int
    type: EventType
    payload: dict[str, Any]


# Sentinel placed into a subscriber queue when it is dropped due to being full.
# SSE event_generator() should check for this object and close the stream.
class _DroppedSentinel:
    """Placed in a subscriber queue when the subscriber has been dropped."""


DROPPED: _DroppedSentinel = _DroppedSentinel()

_RECENT_BUFFER_SIZE = 200


class EventBus:
    """Simple pub/sub event bus for SSE streams.

    Subscribers receive events via asyncio.Queue.  All methods are called
    from the same event-loop thread, so list mutations are safe without
    additional locking — asyncio is single-threaded; there are no await
    points inside publish(), so no other coroutine can interleave.

    A bounded ring-buffer (_recent) allows late-joining subscribers to
    replay the last N events via get_recent_events().
    """

    def __init__(self, recent_buffer_size: int = _RECENT_BUFFER_SIZE) -> None:
        self._subscribers: list[asyncio.Queue[DriveEvent | _DroppedSentinel]] = []
        self._seq = 0
        self._recent: deque[DriveEvent] = deque(maxlen=recent_buffer_size)
        self.subscribers_dropped_total: int = 0

    def publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Publish an event to all active subscribers.

        Payload is shallow-copied to prevent post-publish mutation from
        affecting subscribers who haven't consumed the event yet.
        """
        self._seq += 1
        event = DriveEvent(
            seq=self._seq,
            ts=int(time.time() * 1000),
            type=event_type,
            payload=copy.copy(payload),
        )
        self._recent.append(event)

        # Broadcast; drop slow consumers whose queue is full.
        stale: list[asyncio.Queue[DriveEvent | _DroppedSentinel]] = []
        for sub_queue in self._subscribers:
            try:
                sub_queue.put_nowait(event)
            except asyncio.QueueFull:
                # Queue is full — discard the oldest item to make room for the
                # DROPPED sentinel so the subscriber knows it was dropped and can
                # close its SSE stream.  Without this, DROPPED would silently fail
                # and the subscriber would become a zombie that only receives pings.
                try:
                    sub_queue.get_nowait()  # discard oldest
                except asyncio.QueueEmpty:
                    pass
                with contextlib.suppress(asyncio.QueueFull):
                    sub_queue.put_nowait(DROPPED)
                stale.append(sub_queue)
                self.subscribers_dropped_total += 1
            except Exception:
                stale.append(sub_queue)
        for q in stale:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue[DriveEvent | _DroppedSentinel]:
        """Subscribe to events. Returns a queue that receives future events."""
        sub_queue: asyncio.Queue[DriveEvent | _DroppedSentinel] = asyncio.Queue(maxsize=100)
        self._subscribers.append(sub_queue)
        return sub_queue

    def unsubscribe(self, queue: asyncio.Queue[DriveEvent | _DroppedSentinel]) -> None:
        """Unsubscribe from events.

        Safe to call even if the subscriber was already dropped by publish()
        due to a full queue.
        """
        with contextlib.suppress(ValueError):
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        """Return the number of active SSE subscribers."""
        return len(self._subscribers)

    def shutdown_notify(self) -> None:
        """Publish a SHUTDOWN event and clear all subscribers.

        Called during application shutdown so that SSE event_generator()
        loops see the event and close their streams gracefully instead of
        waiting for a TCP-level disconnect.
        """
        self.publish(EventType.SHUTDOWN, {"reason": "server_shutdown"})
        self._subscribers.clear()

    def get_recent_events(self, *, limit: int = _RECENT_BUFFER_SIZE) -> list[DriveEvent]:
        """Return up to *limit* most-recent events from the ring buffer.

        Useful for late-joining SSE clients that want to catch up on
        recent state without waiting for the next publish cycle.
        """
        if limit <= 0:
            return []
        events = list(self._recent)
        if limit < len(events):
            events = events[-limit:]
        return events
