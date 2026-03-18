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
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("fdir")

# ── Constants ─────────────────────────────────────────────────────────
RING_MAX = int(os.getenv("FDIR_RING_MAX", "500"))
LOG_DIR = Path(os.getenv("FDIR_LOG_DIR", "/var/log/camera-fdir"))


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
    node: str = field(default_factory=lambda: os.getenv("HOSTNAME", "unknown"))

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# ── In-memory ring buffer ────────────────────────────────────────────
_ring: Deque[FdirEvent] = deque(maxlen=RING_MAX)
_lock = Lock()


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

    with _lock:
        _ring.append(event)

    # Prometheus counter (lazy import to avoid circular deps)
    try:
        from app.metrics import fdir_events_total
        fdir_events_total.labels(domain=event.domain, severity=event.severity).inc()
    except Exception:
        pass

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
    """Return the last *n* FDIR events as dicts (newest first)."""
    with _lock:
        items = list(_ring)
    return [asdict(e) for e in reversed(items[-n:])]


_PERSIST_MAX_BYTES = int(os.getenv("FDIR_LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB


def _persist(event: FdirEvent) -> None:
    """Append JSON line to disk (best-effort, no crash on failure).

    Rotates fdir.jsonl → fdir.jsonl.1 when file exceeds
    ``FDIR_LOG_MAX_BYTES`` (default 5 MB).  Only one backup is kept.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "fdir.jsonl"
        # Rotate before write to keep the file bounded.
        try:
            if log_path.exists() and log_path.stat().st_size >= _PERSIST_MAX_BYTES:
                backup = LOG_DIR / "fdir.jsonl.1"
                log_path.replace(backup)
        except OSError:
            pass  # rotation failure is non-critical
        with open(log_path, "a") as f:
            f.write(event.to_json() + "\n")
    except Exception:
        pass  # non-critical; ring buffer is the primary store
