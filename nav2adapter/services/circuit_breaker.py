"""
Circuit breaker pattern for transient failure protection.

States:
- CLOSED: normal operation, requests pass through.
- OPEN: fail fast after consecutive failures exceed threshold.
- HALF_OPEN: allow one probe request to test recovery.
"""
import enum
import logging
import threading
import time

from exceptions import DeviceConnectionError

log = logging.getLogger(__name__)


class _State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Lightweight circuit breaker (no async, no threads — pure state machine)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s

        self._state = _State.CLOSED
        self._failure_count: int = 0
        self._opened_at: float = 0.0

    # -- public queries -------------------------------------------------------

    @property
    def state(self) -> str:
        """Return current state as a string (for metrics / logging)."""
        self._maybe_half_open()
        return self._state.value

    def allow_request(self) -> bool:
        """Return True if a request should be attempted."""
        self._maybe_half_open()
        if self._state == _State.OPEN:
            return False
        return True

    def guard(self) -> None:
        """Raise ``DeviceConnectionError`` if the circuit is open."""
        if not self.allow_request():
            raise DeviceConnectionError(
                f"circuit_open: {self.name} — "
                f"{self._failure_count} consecutive failures, "
                f"retry after {self.reset_timeout_s}s"
            )

    # -- recording outcomes ---------------------------------------------------

    def record_success(self) -> None:
        """Reset failure count; transition HALF_OPEN -> CLOSED."""
        if self._state == _State.HALF_OPEN:
            log.info("circuit_breaker %s: HALF_OPEN -> CLOSED (probe succeeded)", self.name)
        if self._failure_count > 0 or self._state != _State.CLOSED:
            self._failure_count = 0
            self._state = _State.CLOSED

    def record_failure(self) -> None:
        """Increment failure count; trip to OPEN if threshold reached."""
        self._failure_count += 1

        if self._state == _State.HALF_OPEN:
            # Probe failed — reopen immediately.
            self._trip_open("probe failed in HALF_OPEN")
            return

        if self._failure_count >= self.failure_threshold:
            self._trip_open(
                f"{self._failure_count} consecutive failures >= threshold {self.failure_threshold}"
            )

    # -- internals ------------------------------------------------------------

    def _trip_open(self, reason: str) -> None:
        self._state = _State.OPEN
        self._opened_at = time.monotonic()
        log.warning("circuit_breaker %s: -> OPEN (%s)", self.name, reason)

    def _maybe_half_open(self) -> None:
        """Transition OPEN -> HALF_OPEN if reset timeout has elapsed."""
        if self._state != _State.OPEN:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.reset_timeout_s:
            log.info(
                "circuit_breaker %s: OPEN -> HALF_OPEN after %.1fs",
                self.name, elapsed,
            )
            self._state = _State.HALF_OPEN


# ── Shared registry ─────────────────────────────────────────────────
_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    reset_timeout_s: float = 30.0,
) -> CircuitBreaker:
    """Return a shared CircuitBreaker for *name* (typically a host key).

    Creates a new instance on first call; returns the existing one after.
    Thread-safe.
    """
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                reset_timeout_s=reset_timeout_s,
            )
        return _registry[name]
