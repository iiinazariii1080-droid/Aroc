"""Per-command force-arrival signalling.

Replaces the shared ``_force_arrived_commands: Dict[str, bool]`` dict
with per-command ``asyncio.Event`` objects.  This eliminates the TOCTOU
race condition that existed with the dict-based approach.

Single-owner semantics: only the transport watcher consumes the event;
only the position poller sets it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

_LOGGER = logging.getLogger(__name__)


class ForceArrivalSignal:
    """Registry of per-command force-arrival Events + dwell timers."""

    def __init__(self) -> None:
        self._events: Dict[str, asyncio.Event] = {}
        self._at_goal_since: Dict[str, float] = {}

    def ensure(self, command_id: str) -> asyncio.Event:
        """Return (or create) the Event for *command_id*."""
        ev = self._events.get(command_id)
        if ev is None:
            ev = asyncio.Event()
            self._events[command_id] = ev
        return ev

    def get(self, command_id: str) -> Optional[asyncio.Event]:
        return self._events.get(command_id)

    def is_set(self, command_id: str) -> bool:
        ev = self._events.get(command_id)
        return ev is not None and ev.is_set()

    def consume(self, command_id: str) -> bool:
        """If the event is set, clear it and return True."""
        ev = self._events.get(command_id)
        if ev is not None and ev.is_set():
            ev.clear()
            return True
        return False

    def signal(self, command_id: str) -> None:
        """Signal force-arrival for *command_id* (position poller calls this)."""
        ev = self.ensure(command_id)
        ev.set()
        self._at_goal_since.pop(command_id, None)

    def cleanup(self, command_id: str) -> None:
        """Remove all tracking state for a completed/cancelled command."""
        self._events.pop(command_id, None)
        self._at_goal_since.pop(command_id, None)

    def clear_all(self) -> None:
        self._events.clear()
        self._at_goal_since.clear()

    # -- Dwell timer helpers -----------------------------------------------

    def start_dwell(self, command_id: str) -> None:
        if command_id not in self._at_goal_since:
            self._at_goal_since[command_id] = time.monotonic()

    def reset_dwell(self, command_id: str) -> None:
        self._at_goal_since.pop(command_id, None)

    def dwell_elapsed(self, command_id: str, threshold_s: float) -> bool:
        start = self._at_goal_since.get(command_id)
        if start is None:
            return False
        return (time.monotonic() - start) >= threshold_s
