"""Structured FDIR (Failure Detection, Isolation, Recovery) event logging.

Every recovery action emits an unambiguous event record: what failed,
when, what was done, and the outcome.  Events are written as JSON lines
to a ring-buffer log file *and* to the Python logging subsystem.

Aligned with deep-research-report §Autonomy:
  "Every recovery action must emit an unambiguous event record."
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

# ── Hostname cached once — gethostname() is a syscall; it never changes ──
_HOSTNAME: str = socket.gethostname()


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class Domain(str, Enum):
    SENSOR = "sensor"
    PIPELINE = "pipeline"
    JANUS = "janus"
    NETWORK = "network"
    TURN = "turn"
    CLIENT = "client"
    SYSTEM = "system"


class RecoveryAction(str, Enum):
    RETRY_HANDLE = "retry_handle"
    RESTART_PIPELINE = "restart_pipeline"
    RESTART_JANUS = "restart_janus"
    USB_RESET = "usb_reset"
    REBOOT_NODE = "reboot_node"
    DEGRADE_PROFILE = "degrade_profile"
    SWITCH_MODE = "switch_mode"
    NONE = "none"


@dataclass(frozen=True)
class FdirEvent:
    """Immutable record of a single FDIR event."""
    timestamp: float
    domain: str
    severity: str
    detection_signal: str
    recovery_action: str
    outcome: str
    details: Dict[str, Any] = field(default_factory=dict)
    # Use the module-level cached hostname instead of calling gethostname()
    # on every event (syscall overhead; hostname is constant during runtime).
    node: str = _HOSTNAME

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# ── In-memory ring buffer (lazy — maxlen from Settings) ─────────────

_ring: Optional[Deque[FdirEvent]] = None
_ring_init_lock = Lock()
_lock = Lock()


def _get_ring() -> Deque[FdirEvent]:
    """Return the ring buffer, initialising it on first use."""
    global _ring
    if _ring is None:
        with _ring_init_lock:
            if _ring is None:
                from app.core.settings import get_settings
                _ring = deque(maxlen=get_settings().fdir_ring_max)
    return _ring


def emit(
    domain: Domain | str,
    severity: Severity | str,
    detection_signal: str,
    recovery_action: RecoveryAction | str,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
) -> FdirEvent:
    """Record an FDIR event (ring buffer + log + optional file)."""
    event = FdirEvent(
        timestamp=time.time(),
        domain=str(domain.value if isinstance(domain, Domain) else domain),
        severity=str(severity.value if isinstance(severity, Severity) else severity),
        detection_signal=detection_signal,
        recovery_action=str(
            recovery_action.value
            if isinstance(recovery_action, RecoveryAction)
            else recovery_action
        ),
        outcome=outcome,
        details=details or {},
    )

    from app.metrics.safe import safe_inc

    ring = _get_ring()
    with _lock:
        if len(ring) == ring.maxlen:
            # Ring buffer is full — oldest event will be dropped
            safe_inc("fdir_events_dropped_total")
        ring.append(event)

    safe_inc("fdir_events_total", labels={"domain": event.domain, "severity": event.severity})

    # Python logger
    log_level = {
        "info": logging.INFO,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(event.severity, logging.INFO)
    logger.log(log_level, "[FDIR] %s", event.to_json())

    # Optional file persistence (best-effort)
    _persist(event)

    return event


def recent(n: int = 50) -> list[dict]:
    """Return the last *n* FDIR events as dicts (newest first).

    The ring buffer holds at most ``fdir_ring_max`` events (default 500).
    Requesting n > fdir_ring_max silently returns at most fdir_ring_max entries.
    Monitor ``camstack_fdir_events_dropped_total`` to detect overflow.
    """
    ring = _get_ring()
    with _lock:
        items = list(ring)
    return [asdict(e) for e in reversed(items[-n:])]


_persist_error_logged = False
# Batched fsync: track pending writes since last fsync.  On embedded
# systems with SD-card, fsync takes 10-50ms.  Syncing every event
# creates backpressure during event storms.  Instead we fsync every
# _FSYNC_BATCH_SIZE events or every _FSYNC_INTERVAL_SEC seconds.
_persist_pending: int = 0
_persist_last_fsync: float = 0.0
_FSYNC_BATCH_SIZE = 8
_FSYNC_INTERVAL_SEC = 2.0

# Persistent file handle — kept open across events to avoid
# open/close overhead on every FDIR event (important on embedded
# systems with slow SD-card I/O during event storms).
# Protected by _persist_lock (leaf lock — never acquires another lock).
_persist_lock = Lock()
_persist_fh: Any = None
_persist_fh_path: Optional[str] = None


def _close_persist_handle() -> None:
    """Close the persistent file handle (if open)."""
    global _persist_fh, _persist_fh_path
    if _persist_fh is not None:
        try:
            _persist_fh.close()
        except Exception:
            pass
        _persist_fh = None
        _persist_fh_path = None


def _persist(event: FdirEvent) -> None:
    """Append JSON line to disk (best-effort, no crash on failure).

    Rotates fdir.jsonl → fdir.jsonl.1 when file exceeds
    ``fdir_log_max_bytes`` (default 5 MB).  Only one backup is kept.
    Logs the first persistence error so operators know the audit trail
    is degraded; subsequent errors are suppressed to avoid log storms.

    Uses a persistent file handle to avoid open/close per event.
    Thread-safe: all file handle access is serialised by ``_persist_lock``
    so the thermal daemon thread and async watchdog task can call
    ``emit()`` concurrently without corrupting the audit log.

    fsync is batched: the file is flushed on every write (data reaches
    kernel page cache), but ``fsync()`` is called only every
    ``_FSYNC_BATCH_SIZE`` events or every ``_FSYNC_INTERVAL_SEC`` seconds.
    """
    global _persist_error_logged, _persist_pending, _persist_last_fsync
    global _persist_fh, _persist_fh_path
    with _persist_lock:
        try:
            from app.core.settings import get_settings
            settings = get_settings()
            log_dir = settings.fdir_log_dir
            max_bytes = settings.fdir_log_max_bytes

            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "fdir.jsonl"
            log_path_str = str(log_path)

            # Rotate if needed — close handle, rename, reopen
            try:
                if log_path.exists() and log_path.stat().st_size >= max_bytes:
                    _close_persist_handle()
                    backup = log_dir / "fdir.jsonl.1"
                    log_path.replace(backup)
                    _persist_pending = 0  # rotation acts as implicit sync point
            except OSError:
                pass  # rotation failure is non-critical

            # Open/reopen handle if needed
            if _persist_fh is None or _persist_fh.closed or _persist_fh_path != log_path_str:
                _close_persist_handle()
                _persist_fh = open(log_path, "a")
                _persist_fh_path = log_path_str

            _persist_fh.write(event.to_json() + "\n")
            _persist_fh.flush()
            _persist_pending += 1
            now = time.monotonic()
            if (_persist_pending >= _FSYNC_BATCH_SIZE
                    or now - _persist_last_fsync >= _FSYNC_INTERVAL_SEC):
                os.fsync(_persist_fh.fileno())
                _persist_pending = 0
                _persist_last_fsync = now
            _persist_error_logged = False  # reset on success
        except Exception:
            _close_persist_handle()  # reset handle on error
            if not _persist_error_logged:
                logger.warning(
                    "FDIR event persistence failed — audit trail degraded "
                    "(further errors suppressed until next success)",
                    exc_info=True,
                )
                _persist_error_logged = True


def _reset_for_tests() -> None:
    """Reset module-level state for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    _close_persist_handle()
    with _lock:
        if _ring is not None:
            _ring.clear()
