"""Asynchronous write-ahead queue for persistence stores.

Decouples domain state mutations from I/O: state changes are immediately
visible in-memory; persistence happens asynchronously in a background task.

PersistenceWriter owns:
  - the asyncio Queue of pending ops
  - the background writer task (start/stop lifecycle)
  - best-effort and critical enqueue semantics
  - the underlying persistence store (exposed for direct reads)

StateStore is responsible for domain state only -- it delegates all
persistence I/O through an injected PersistenceWriter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from services.persistence_store import JsonPersistenceStore as PersistenceStore
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)


class PersistenceWriter:
    """Background writer that decouples in-memory state mutations from file I/O."""

    def __init__(self, store: PersistenceStore) -> None:
        # Exposed for direct reads (load, load_sessions) by StateStore.
        self.store: PersistenceStore = store
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start background writer task (requires a running event loop)."""
        if self._running:
            return
        self._running = True
        self._queue = asyncio.Queue(maxsize=1000)
        self._task = asyncio.create_task(self._writer_loop())

    async def stop(self) -> None:
        """Stop writer, draining pending writes best-effort within 3 s."""
        self._running = False
        if self._task and self._queue:
            try:
                deadline = 3.0
                t0 = time.monotonic()
                while not self._queue.empty() and (time.monotonic() - t0) < deadline:
                    try:
                        op = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        await self._exec_op(op)
                    except Exception:
                        _LOGGER.debug("Persistence drain: failed to flush op", exc_info=True)
                remaining = self._queue.qsize()
                if remaining:
                    _LOGGER.warning(
                        "Persistence shutdown: %d ops could not be flushed", remaining
                    )
            except Exception:
                _LOGGER.debug("Persistence drain failed", exc_info=True)

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._queue = None

    # -- Enqueue -----------------------------------------------------------

    def enqueue(self, op: Dict[str, Any]) -> None:
        """Non-blocking best-effort enqueue. Drops silently when queue is full."""
        if not self._queue:
            reliability_metrics.inc("persistence.enqueue.skipped_no_queue")
            return
        try:
            self._queue.put_nowait(op)
            reliability_metrics.inc("persistence.enqueue.ok")
        except asyncio.QueueFull:
            reliability_metrics.inc("persistence.enqueue.drop_queue_full")
            _LOGGER.error(
                "Persistence enqueue dropped (queue full, maxsize=%d). "
                "In-memory state is intact but this write may not survive a crash.",
                self._queue.maxsize,
            )

    async def enqueue_critical(self, op: Dict[str, Any]) -> None:
        """Blocking enqueue with 2 s timeout for state-critical operations.

        Used for register_command and clear_transport to prevent silent state
        divergence after a crash when the persistence queue is saturated.
        Falls back to a warning -- never raises -- so in-memory state remains
        the source of truth.
        """
        if not self._queue:
            reliability_metrics.inc("persistence.enqueue.skipped_no_queue")
            return
        try:
            await asyncio.wait_for(self._queue.put(op), timeout=2.0)
            reliability_metrics.inc("persistence.enqueue.ok")
        except asyncio.TimeoutError:
            reliability_metrics.inc("persistence.enqueue.drop_queue_full")
            _LOGGER.warning(
                "Critical persistence op %r timed out (queue full, 2 s). "
                "In-memory state is intact but this command may not survive a crash.",
                op.get("type"),
            )

    # -- Internal ----------------------------------------------------------

    async def _exec_op(self, op: Dict[str, Any]) -> None:
        """Execute a single persistence operation against the store."""
        op_type = op.get("type")
        started = time.perf_counter()
        if op_type == "upsert":
            await self.store.upsert(op["data"])
            reliability_metrics.inc("persistence.writer.upsert.ok")
            reliability_metrics.observe_duration(
                "persistence.writer.upsert.latency_s", time.perf_counter() - started
            )
        elif op_type == "upsert_session":
            await self.store.upsert_session(op["data"])
            reliability_metrics.inc("persistence.writer.upsert_session.ok")
            reliability_metrics.observe_duration(
                "persistence.writer.upsert_session.latency_s", time.perf_counter() - started
            )
        elif op_type == "delete":
            await self.store.delete(op["command_id"])
            reliability_metrics.inc("persistence.writer.delete.ok")
            reliability_metrics.observe_duration(
                "persistence.writer.delete.latency_s", time.perf_counter() - started
            )
        elif op_type == "delete_session":
            await self.store.delete_session(op["command_id"])
            reliability_metrics.inc("persistence.writer.delete_session.ok")
            reliability_metrics.observe_duration(
                "persistence.writer.delete_session.latency_s", time.perf_counter() - started
            )
        else:
            reliability_metrics.inc("persistence.writer.unknown_op")

    async def _writer_loop(self) -> None:
        """Background loop that dequeues and executes persistence operations."""
        if not self._queue:
            return
        try:
            while self._running:
                try:
                    op = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    reliability_metrics.inc("persistence.writer.dequeue")
                    try:
                        await self._exec_op(op)
                    except Exception:
                        # Never let persistence failures break in-memory runtime.
                        reliability_metrics.inc("persistence.writer.failed")
                        _LOGGER.warning("Persistence write failed", exc_info=True)
                except asyncio.TimeoutError:
                    reliability_metrics.inc("persistence.writer.timeout")
                    continue
        except asyncio.CancelledError:
            pass
