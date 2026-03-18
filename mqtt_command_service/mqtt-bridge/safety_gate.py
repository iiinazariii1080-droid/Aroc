"""SafetyGate: cached MQTT safety state with fail-closed heartbeat timeout."""

import json
import logging
import threading
import time
from typing import Any

from paho.mqtt.client import MQTTMessage

from shared.metrics import safety_gate_locked

logger = logging.getLogger(__name__)

_HEARTBEAT_TIMEOUT_S = 60.0
# Minimum interval between repeated "no safety data" / "stale heartbeat" warnings.
_WARN_INTERVAL_S = 60.0


class SafetyGate:
    """Cached MQTT safety state with fail-closed heartbeat timeout.

    State transitions:
        - On init: no safety data; commands are ALLOWED during startup_grace period.
        - After startup_grace expires without a heartbeat: commands are BLOCKED (fail-closed).
        - After handle_safety_message(): state cached; is_locked() reflects payload.
        - If heartbeat goes stale (>heartbeat_timeout): is_locked() returns True.
    """

    def __init__(
        self,
        heartbeat_timeout: float = _HEARTBEAT_TIMEOUT_S,
        startup_grace_seconds: float = 0.0,
    ) -> None:
        self._safety_state: dict[str, Any] | None = None
        self._safety_state_ts: float = 0.0
        self._lock = threading.Lock()
        self._heartbeat_timeout = heartbeat_timeout
        self._startup_grace_seconds = startup_grace_seconds
        self._created_at = time.time()
        self._last_no_data_warn_ts: float = 0.0
        self._last_stale_warn_ts: float = 0.0
        self._grace_logged = False

    def handle_safety_message(self, message: MQTTMessage) -> None:
        """Cache safety state from nav2adapter retained topic."""
        try:
            payload = json.loads(message.payload.decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                with self._lock:
                    self._safety_state = payload
                    self._safety_state_ts = time.time()
                if payload.get("safety_lockout"):
                    logger.warning(
                        "[bridge] Safety lockout active: reason=%s",
                        payload.get("reason"),
                    )
                else:
                    logger.debug("[bridge] Safety state: OK")
        except Exception as e:
            logger.warning("[bridge] Failed to parse safety state: %s", e)

    def is_locked(self) -> bool:
        """Check if robot is in safety lockout (fail-closed).

        Returns True (locked) when:
        - No safety message has been received AND startup grace period has expired.
        - The last safety heartbeat is stale (older than heartbeat_timeout).
        - The cached safety payload has safety_lockout=True.

        During the startup grace period, if no safety message has been received
        yet, commands are ALLOWED to prevent permanent lockout in deployments
        without a safety publisher (e.g. no nav2adapter).

        Warning-rate timestamps are updated under ``_lock`` to prevent a race
        where two concurrent callers both pass the interval check and both emit
        the throttled warning.  Logging happens after releasing the lock to
        avoid holding it during I/O.
        """
        now = time.time()
        warn_no_data = False
        warn_stale_age: float | None = None
        log_grace = False
        with self._lock:
            state = self._safety_state
            ts = self._safety_state_ts
            if state is None:
                # No safety data yet — check grace period.
                elapsed = now - self._created_at
                if elapsed < self._startup_grace_seconds:
                    # Grace period active: allow commands.
                    if not self._grace_logged:
                        self._grace_logged = True
                        log_grace = True
                    result = False
                else:
                    # Grace period expired — fail-closed.
                    if now - self._last_no_data_warn_ts >= _WARN_INTERVAL_S:
                        self._last_no_data_warn_ts = now
                        warn_no_data = True
                    result = True
            else:
                age = now - ts
                if age > self._heartbeat_timeout:
                    if now - self._last_stale_warn_ts >= _WARN_INTERVAL_S:
                        self._last_stale_warn_ts = now
                        warn_stale_age = age
                    result = True
                else:
                    result = bool(state.get("safety_lockout", False))
        # Emit warnings outside the lock (logging may acquire its own locks).
        if log_grace:
            logger.info(
                "[bridge] Safety gate startup grace period active (%.0fs) — commands allowed while waiting for safety heartbeat",
                self._startup_grace_seconds,
            )
        if warn_no_data:
            logger.warning("[bridge] No safety state received yet — commands blocked (fail-closed)")
        if warn_stale_age is not None:
            logger.warning(
                "[bridge] Safety heartbeat stale (%.0fs ago), assuming lockout",
                warn_stale_age,
            )
        safety_gate_locked.labels(service="mqtt-bridge").set(int(result))
        return result

    @property
    def state(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._safety_state) if self._safety_state is not None else None

    @property
    def state_timestamp(self) -> float:
        """Unix timestamp of the last received safety heartbeat (0.0 if none yet)."""
        with self._lock:
            return self._safety_state_ts
