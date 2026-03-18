"""Safety state tracker — detects E-Stop / safety relay transitions and monitors safety state.

Monitors Symovo ``state_flags`` on each status poll and emits structured
safety state when a transition occurs or as a periodic heartbeat.

Design:
- Hooks into the existing ``_watch_status_loop`` cycle (no extra polling).
- Publishes to ``aroc/robot/{id}/status/safety`` with QoS=1, retained=True.
- Heartbeat every ``HEARTBEAT_INTERVAL_S`` even when state is unchanged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)

#: Keys in Symovo ``state_flags`` that indicate safety lockout.
_LOCKOUT_KEYS = {
    "emergency_stop": "estop",
    "sfuse_blown": "sfuse_blown",
}

#: How often to re-publish even without a transition (seconds).
HEARTBEAT_INTERVAL_S: float = 10.0


@dataclass
class SafetyState:
    """Snapshot of the robot safety state."""

    safety_lockout: bool
    reason: Optional[str]  # estop | relay_open | sfuse_blown | None
    state_flags: Dict[str, Any]
    since_ts: Optional[str]  # ISO-8601 when lockout began (None if not locked)
    ts: str  # ISO-8601 timestamp of this snapshot
    recovery_available: bool  # True when relay is restored but not yet recovered

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safety_lockout": self.safety_lockout,
            "reason": self.reason,
            "state_flags": self.state_flags,
            "since_ts": self.since_ts,
            "ts": self.ts,
            "recovery_available": self.recovery_available,
        }


class SafetyStateTracker:
    """Tracks safety state transitions from Symovo ``state_flags``."""

    def __init__(self) -> None:
        self._locked_out: bool = False
        self._reason: Optional[str] = None
        self._locked_since: Optional[str] = None
        self._last_publish_time: float = 0.0
        self._last_state_flags: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API — called from StatusPublisher._watch_status_loop
    # ------------------------------------------------------------------

    def evaluate(self, raw_status: Dict[str, Any]) -> Optional[SafetyState]:
        """Evaluate raw Symovo status and return a ``SafetyState`` if it should be published.

        Returns ``None`` when nothing changed and heartbeat is not yet due.
        """
        state_flags = raw_status.get("state_flags", {}) if isinstance(raw_status, dict) else {}
        self._last_state_flags = state_flags

        locked_out, reason = self._check_lockout(state_flags)

        transition = locked_out != self._locked_out
        heartbeat_due = (time.monotonic() - self._last_publish_time) >= HEARTBEAT_INTERVAL_S

        if transition:
            now_iso = _now_iso()
            if locked_out and not self._locked_out:
                # Entering lockout
                self._locked_since = now_iso
                _LOGGER.warning(
                    "Safety lockout ACTIVATED: reason=%s, state_flags=%s",
                    reason,
                    {k: v for k, v in state_flags.items() if not isinstance(v, dict)},
                )
            elif not locked_out and self._locked_out:
                # Leaving lockout
                _LOGGER.info("Safety lockout CLEARED (was: %s)", self._reason)
                self._locked_since = None

            self._locked_out = locked_out
            self._reason = reason

        if transition or heartbeat_due:
            self._last_publish_time = time.monotonic()
            return self.current_state()

        return None

    def current_state(self) -> SafetyState:
        """Return current safety state snapshot (for HTTP endpoint)."""
        recovery_available = False
        if self._locked_out and self._reason in ("estop", "relay_open"):
            # Recovery is available when relay is restored
            relay_closed = self._last_state_flags.get("safety_relais_closed_state", False)
            recovery_available = bool(relay_closed)

        return SafetyState(
            safety_lockout=self._locked_out,
            reason=self._reason,
            state_flags=self._last_state_flags,
            since_ts=self._locked_since,
            ts=_now_iso(),
            recovery_available=recovery_available,
        )

    @property
    def is_locked_out(self) -> bool:
        return self._locked_out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _check_lockout(state_flags: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Determine if robot is in safety lockout from ``state_flags``.

        Returns ``(is_locked, reason)``.
        """
        if not state_flags:
            # Fail-closed: no state_flags → assume lockout.
            return True, "unknown"

        # Priority 1: explicit E-Stop
        if state_flags.get("emergency_stop", False):
            return True, "estop"

        # Priority 2: safety relay open
        # Some controllers report this flag; if absent, skip.
        relay_key = "safety_relais_closed_state"
        if relay_key in state_flags and not state_flags[relay_key]:
            return True, "relay_open"

        # Priority 3: software fuse blown
        if state_flags.get("sfuse_blown", False):
            return True, "sfuse_blown"

        return False, None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
