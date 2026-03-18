"""Command deduplication and history tracking.

Thread-safe storage for:
- In-flight command guard (prevents duplicate execution)
- Command result history (enables cached-replay on re-delivery)

Uses a single lock for both in-flight set and history dict to prevent
race conditions between finish() and try_start() on the same command_id.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel object used to mark commands that finished without a stored result.
# Using a dedicated object avoids collision with user payload keys.
_DEDUP_SENTINEL = object()


class CommandDeduplicator:
    """Thread-safe command deduplication and result history.

    Tracks which command IDs are currently being processed
    and caches recent results for replay on duplicate deliveries.

    **Ordering preference**: callers SHOULD call ``store(id, payload)``
    before ``finish(id)`` so that concurrent ``get()`` can return a
    cached result.  If ``finish`` is called without a prior ``store``
    (e.g. the command failed validation), a sentinel is recorded in
    history so that duplicate deliveries see the command as "already
    processed" rather than re-executing it.
    """

    def __init__(
        self,
        history_ttl: float = 300.0,
        max_history: int = 1000,
    ) -> None:
        self._commands_in_flight: set[str] = set()
        self._history: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._history_ttl = history_ttl
        self._max_history = max_history

    # ---- In-flight guard ------------------------------------------------

    def try_start(self, command_id: str) -> bool:
        """Register *command_id* as in-flight.

        Returns ``True`` if the command was registered (not a duplicate).
        Returns ``False`` if it is already running.
        """
        if not command_id:
            return True
        with self._lock:
            if command_id in self._commands_in_flight:
                return False
            self._commands_in_flight.add(command_id)
            return True

    def finish(self, command_id: str | None) -> None:
        """Remove *command_id* from the in-flight set.

        If no result was stored via ``store()`` before this call,
        a sentinel entry is recorded in history to prevent
        duplicate re-execution on message re-delivery.
        """
        if not command_id:
            return
        with self._lock:
            was_in_flight = command_id in self._commands_in_flight
            self._commands_in_flight.discard(command_id)
            # If the command was in-flight but no result was stored,
            # record a sentinel so get() returns a "done, no result" marker
            # instead of treating a re-delivery as a brand-new command.
            if was_in_flight and command_id not in self._history:
                self._history[command_id] = {
                    "payload": _DEDUP_SENTINEL,
                    "timestamp": time.time(),
                }

    @property
    def in_flight_count(self) -> int:
        """Number of commands currently in flight."""
        with self._lock:
            return len(self._commands_in_flight)

    # ---- Result history --------------------------------------------------

    def store(self, command_id: str, payload: dict[str, Any]) -> None:
        """Store a deep-copied result for *command_id*."""
        if not command_id:
            return
        snapshot = copy.deepcopy(payload)
        now = time.time()
        with self._lock:
            self._history[command_id] = {
                "payload": snapshot,
                "timestamp": now,
            }
            self._cleanup_locked(now)

    def get(self, command_id: str) -> dict[str, Any] | None:
        """Retrieve cached result for *command_id*, or ``None`` if expired/absent."""
        if not command_id:
            return None
        with self._lock:
            entry = self._history.get(command_id)
            if not entry:
                return None
            if time.time() - entry["timestamp"] > self._history_ttl:
                self._history.pop(command_id, None)
                return None
            payload = entry["payload"]
            if payload is _DEDUP_SENTINEL:
                return None
            result: dict[str, Any] = copy.deepcopy(payload)
            return result

    def was_processed(self, command_id: str) -> bool:
        """Return True if *command_id* exists in history (including sentinels) and is not expired."""
        if not command_id:
            return False
        with self._lock:
            entry = self._history.get(command_id)
            if not entry:
                return False
            if time.time() - entry["timestamp"] > self._history_ttl:
                self._history.pop(command_id, None)
                return False
            return True

    def _cleanup_locked(self, now: float) -> None:
        """Evict expired entries and enforce size cap (caller must hold lock)."""
        cutoff = now - self._history_ttl
        to_delete = [cid for cid, entry in self._history.items() if entry["timestamp"] < cutoff]
        for cid in to_delete:
            self._history.pop(cid, None)

        if len(self._history) > self._max_history:
            sorted_keys = sorted(
                self._history,
                key=lambda k: self._history[k]["timestamp"],
            )
            excess = len(self._history) - self._max_history
            for k in sorted_keys[:excess]:
                self._history.pop(k, None)
