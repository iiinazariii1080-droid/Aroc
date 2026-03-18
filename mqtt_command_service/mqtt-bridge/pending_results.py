"""PendingResultQueue: queues MQTT responses when broker is disconnected.

Provides safety-aware eviction — e-stop and safety_alert results are
protected from eviction when the queue is full.

Known limitation — volatile in-memory queue:
    All pending results live in memory.  A process crash or restart while
    MQTT is disconnected will permanently lose queued responses.  This is
    acceptable for single-robot deployments where the hub can re-query
    task status via the robot service.  For fleet / HA deployments,
    replace the in-memory dict with a disk-persisted store (e.g. SQLite WAL).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from mqtt_publisher import PublishResult

from shared.constants import MAX_TASK_RESULT_QUEUE

logger = logging.getLogger(__name__)

_SAFETY_COMMAND_NAMES = frozenset({"estop", "safety_alert"})


class PendingResultQueue:
    """Thread-safe queue for pending MQTT results with safety-aware eviction."""

    def __init__(
        self,
        shutdown_event: threading.Event,
        publish_json_fn: Callable[[str, dict[str, Any]], "PublishResult"],
        get_resp_topic_fn: Callable[[str], str],
        mqtt_is_connected_fn: Callable[[], bool],
    ) -> None:
        self._shutdown = shutdown_event
        self._publish_json = publish_json_fn
        self._get_resp_topic = get_resp_topic_fn
        self._is_connected = mqtt_is_connected_fn
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._flush_thread: threading.Thread | None = None

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def queue(self, request_id: str, result: dict[str, Any]) -> None:
        """Queue a result for later delivery. Safety-critical results are protected from eviction."""
        if not request_id:
            return
        with self._lock:
            if request_id not in self._pending and len(self._pending) >= MAX_TASK_RESULT_QUEUE:
                # Evict oldest non-safety entry; safety-critical results (e-stop) are protected
                evict_key = None
                for k, v in self._pending.items():
                    if v.get("command_name") not in _SAFETY_COMMAND_NAMES:
                        evict_key = k
                        break
                if evict_key is None:
                    # All entries are safety-critical — evict oldest anyway
                    evict_key = next(iter(self._pending))
                del self._pending[evict_key]
                logger.error(
                    "[bridge] Pending results queue full (%d), evicted entry: %s — "
                    "client will never receive this result. Consider increasing "
                    "MAX_TASK_RESULT_QUEUE or investigating MQTT connectivity.",
                    MAX_TASK_RESULT_QUEUE,
                    evict_key,
                )
            self._pending[request_id] = result

    def flush(self) -> None:
        """Attempt to deliver all pending results via MQTT."""
        if not self._flush_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                if not self._pending:
                    return
                results_to_send = dict(self._pending)
            for request_id, result in results_to_send.items():
                try:
                    service = result.get("service", "unknown")
                    topic = self._get_resp_topic(service)
                    pub_result = self._publish_json(topic, result)
                    # Remove from queue on success OR permanent failure (too large).
                    if pub_result in (PublishResult.SUCCESS, PublishResult.PAYLOAD_TOO_LARGE):
                        if pub_result == PublishResult.PAYLOAD_TOO_LARGE:
                            logger.error(
                                "[bridge] Pending result dropped permanently (too large): request_id=%s",
                                request_id,
                            )
                        with self._lock:
                            if self._pending.get(request_id) is result:
                                del self._pending[request_id]
                except Exception as e:
                    logger.error("Failed to flush result for %s: %s", request_id, e)
        finally:
            self._flush_lock.release()

    def start_flush_thread(self) -> None:
        """Start a background thread that periodically flushes pending results."""
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="bridge-pending-flush",
            daemon=True,
        )
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Periodically attempt to flush pending results (every 10s)."""
        while not self._shutdown.wait(timeout=10.0):
            if self.has_pending and self._is_connected():
                self.flush()
