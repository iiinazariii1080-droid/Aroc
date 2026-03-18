"""
In-process event bus for ack/state/result events.

Used to broadcast events to:
- Event consumer
- HTTP SSE subscribers
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Set

from domain.events import AnyEvent
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)


class EventBus:
    """Simple pub/sub bus with per-subscriber asyncio.Queue."""

    def __init__(self, queue_size: int = 1000):
        self._subscribers: Set[asyncio.Queue[AnyEvent]] = set()
        # Per-subscriber drop-log rate limiter: maps queue id -> last log timestamp.
        self._last_drop_log_time: dict[int, float] = {}
        self._lock = asyncio.Lock()
        self._queue_size = queue_size
        self._drop_log_interval: float = 10.0

    async def publish(self, event: AnyEvent) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        reliability_metrics.inc("eventbus.publish.calls")
        for q in subscribers:
            # Best-effort: drop oldest on overflow
            if q.full():
                try:
                    _ = q.get_nowait()
                    reliability_metrics.inc("eventbus.publish.drop_oldest")
                    now = time.monotonic()
                    qid = id(q)
                    if now - self._last_drop_log_time.get(qid, 0.0) >= self._drop_log_interval:
                        _LOGGER.warning(
                            "EventBus: dropped oldest event for subscriber %d (queue full, size=%d). "
                            "Event type: %s",
                            qid,
                            self._queue_size,
                            getattr(event, 'type', type(event).__name__),
                        )
                        self._last_drop_log_time[qid] = now
                except Exception:
                    reliability_metrics.inc("eventbus.publish.drop_failed")
            try:
                q.put_nowait(event)
                reliability_metrics.inc("eventbus.publish.delivered")
            except Exception:
                # ignore broken subscriber
                reliability_metrics.inc("eventbus.publish.delivery_failed")

    async def subscribe(self) -> asyncio.Queue[AnyEvent]:
        q: asyncio.Queue[AnyEvent] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[AnyEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(q)
            self._last_drop_log_time.pop(id(q), None)

    async def drain(self, q: asyncio.Queue[AnyEvent], max_items: int = 100) -> list[AnyEvent]:
        """Drain up to *max_items* from a subscriber queue (non-blocking)."""
        items: list[AnyEvent] = []
        for _ in range(max_items):
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items
