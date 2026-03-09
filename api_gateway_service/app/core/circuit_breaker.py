"""
Per-service circuit breaker.

States
------
CLOSED  – normal operation, requests pass through.
OPEN    – service is considered down; requests are rejected immediately
          with no network call for ``recovery_timeout_s`` seconds.
HALF-OPEN – one probe request is allowed through.
            If it succeeds → CLOSED, if it fails → OPEN again.

Usage (inside proxy)::

    from app.core.circuit_breaker import circuit_breakers

    cb = circuit_breakers.get(service)
    if not cb.allow_request():
        return JSONResponse(status_code=502, ...)
    try:
        resp = await stream_request(...)
        cb.record_success()
    except ...:
        cb.record_failure()
        ...
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Dict

logger = logging.getLogger(__name__)


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ── Tunables (env-overridable) ──────────────────────────────────
FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
RECOVERY_TIMEOUT_S = float(os.getenv("CB_RECOVERY_TIMEOUT", "15.0"))
SUCCESS_THRESHOLD = int(os.getenv("CB_SUCCESS_THRESHOLD", "1"))


@dataclass
class CircuitBreaker:
    """Lightweight per-service circuit breaker (sync-safe, single-process)."""

    service: str
    failure_threshold: int = FAILURE_THRESHOLD
    recovery_timeout_s: float = RECOVERY_TIMEOUT_S
    success_threshold: int = SUCCESS_THRESHOLD

    _state: _State = field(default=_State.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _success_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _half_open_since: float = field(default=0.0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    # ── Public API ──────────────────────────────────────────────

    def allow_request(self) -> bool:
        """Return *True* if the request should be forwarded upstream."""
        with self._lock:
            if self._state is _State.CLOSED:
                return True

            if self._state is _State.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout_s:
                    self._state = _State.HALF_OPEN
                    self._success_count = 0
                    self._half_open_since = time.monotonic()
                    logger.info("Circuit %s → HALF_OPEN (probing)", self.service)
                    return True
                return False

            # HALF_OPEN – timeout back to OPEN if probe hangs
            if time.monotonic() - self._half_open_since >= self.recovery_timeout_s:
                self._state = _State.OPEN
                self._last_failure_time = time.monotonic()
                logger.warning("Circuit %s → OPEN (probe timeout)", self.service)
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state is _State.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = _State.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit %s → CLOSED", self.service)
            else:
                # Reset failure count on any success in CLOSED state
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state is _State.HALF_OPEN:
                self._state = _State.OPEN
                logger.warning("Circuit %s → OPEN (probe failed)", self.service)
            elif self._failure_count >= self.failure_threshold:
                self._state = _State.OPEN
                logger.warning(
                    "Circuit %s → OPEN after %d consecutive failures",
                    self.service,
                    self._failure_count,
                )

    def describe(self) -> Dict:
        with self._lock:
            return {
                "service": self.service,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "last_failure_age_s": round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time
                else None,
            }


# ── Global registry (one CB per service name) ──────────────────
_breakers: Dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_breaker(service: str) -> CircuitBreaker:
    """Get or create a CircuitBreaker for *service*."""
    if service not in _breakers:
        with _registry_lock:
            if service not in _breakers:
                _breakers[service] = CircuitBreaker(service=service)
    return _breakers[service]


def all_breakers() -> Dict[str, Dict]:
    """Snapshot of every known breaker (for health endpoints)."""
    return {name: cb.describe() for name, cb in _breakers.items()}
