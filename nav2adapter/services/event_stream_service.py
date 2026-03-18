"""EventStreamService -- owns SSE connection tracking and long-poll queues.

Replaces module-level mutable state in routes/aehub.py with a proper
service class managed by AppServices lifecycle (start/stop).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from services.event_bus import EventBus
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)

_POLL_TTL = 120  # seconds before idle poll queue is removed


class EventStreamService:
    """Manages SSE client tracking and per-client poll queues."""

    SSE_MAX_CLIENTS = 100
    POLL_MAX_CLIENTS = 200

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._sse_active = 0
        self._sse_lock = asyncio.Lock()
        self._poll_queues: Dict[str, tuple] = {}  # client_id -> (queue, last_access_monotonic)
        self._poll_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background poll-queue cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="poll_queue_cleanup")

    async def stop(self) -> None:
        """Cancel the cleanup task and unsubscribe any remaining poll queues."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None

        # Clean up any lingering poll queues.
        async with self._poll_lock:
            for client_id, (q, _) in list(self._poll_queues.items()):
                try:
                    await self._event_bus.unsubscribe(q)
                except Exception:
                    reliability_metrics.inc("event_stream.unsubscribe.failed")
            self._poll_queues.clear()

    async def sse_try_increment(self) -> bool:
        """Increment active SSE counter. Returns False if limit reached."""
        async with self._sse_lock:
            if self._sse_active >= self.SSE_MAX_CLIENTS:
                return False
            self._sse_active += 1
            return True

    async def sse_decrement(self) -> None:
        """Decrement active SSE counter."""
        async with self._sse_lock:
            self._sse_active -= 1

    async def get_poll_queue(self, client_id: str) -> asyncio.Queue:
        """Return (or create) a per-client event queue for long-polling.

        Raises RuntimeError if the per-client cap is reached.
        """
        import time as _time
        async with self._poll_lock:
            now = _time.monotonic()
            # Inline eviction (cheap: only runs while holding the lock anyway)
            stale = [k for k, (_, t) in self._poll_queues.items() if now - t > _POLL_TTL]
            for k in stale:
                await self._event_bus.unsubscribe(self._poll_queues.pop(k)[0])
            if client_id in self._poll_queues:
                q, _ = self._poll_queues[client_id]
                self._poll_queues[client_id] = (q, now)
                return q
            if len(self._poll_queues) >= self.POLL_MAX_CLIENTS:
                raise RuntimeError("Too many polling clients")
            q = await self._event_bus.subscribe()
            self._poll_queues[client_id] = (q, now)
            return q

    async def _cleanup_loop(self) -> None:
        """Periodically remove stale poll queues."""
        import time as _time
        while True:
            try:
                await asyncio.sleep(60)
                async with self._poll_lock:
                    now = _time.monotonic()
                    stale = [k for k, (_, t) in self._poll_queues.items() if now - t > _POLL_TTL]
                    for k in stale:
                        await self._event_bus.unsubscribe(self._poll_queues.pop(k)[0])
                    if stale:
                        _LOGGER.debug("Cleaned %d stale poll queues", len(stale))
            except asyncio.CancelledError:
                break
            except Exception:
                reliability_metrics.inc("event_stream.cleanup.failed")
