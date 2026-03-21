"""System Operating Modes for rover-grade camera streaming.

Implements explicit system modes as required by deep-research-report
§Autonomy: "Define explicit system modes (Nominal / Degraded /
Local-only / Safe).  Each mode has: which streams are published,
bitrate/FPS caps, which dependencies are required, exit criteria
back to nominal."

Modes form a lattice:
    NOMINAL  →  DEGRADED  →  LOCAL_ONLY  →  SAFE
    (any transition back requires explicit promotion)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.services.fdir_events import Domain, RecoveryAction, Severity, emit

logger = logging.getLogger("system_mode")

# Lazy metric imports — avoids circular import at module load time
_metrics_loaded = False
_system_mode_gauge = None
_mode_transitions_counter = None
_metrics_lock = threading.Lock()


def _ensure_metrics():  # noqa: D401
    global _metrics_loaded, _system_mode_gauge, _mode_transitions_counter
    if _metrics_loaded:
        return
    with _metrics_lock:
        if _metrics_loaded:
            return
        try:
            from app.metrics import system_mode as _g, mode_transitions_total as _c
            _system_mode_gauge = _g
            _mode_transitions_counter = _c
        except Exception:  # pragma: no cover
            pass
        _metrics_loaded = True


class SystemMode(str, Enum):
    """Operating modes ordered by degradation level."""
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    LOCAL_ONLY = "local_only"
    SAFE = "safe"

    @property
    def level(self) -> int:
        return {
            SystemMode.NOMINAL: 0,
            SystemMode.DEGRADED: 1,
            SystemMode.LOCAL_ONLY: 2,
            SystemMode.SAFE: 3,
        }[self]


@dataclass
class ModePolicy:
    """Policy enforced per mode."""
    streams_enabled: bool = True
    max_fps: int = 30
    max_bitrate_kbps: int = 4000
    require_turn: bool = True
    require_uplink: bool = True
    description: str = ""


# ── Default policies per mode ─────────────────────────────────────────
MODE_POLICIES: Dict[SystemMode, ModePolicy] = {
    SystemMode.NOMINAL: ModePolicy(
        streams_enabled=True,
        max_fps=30,
        max_bitrate_kbps=4000,
        require_turn=True,
        require_uplink=True,
        description="All streams active, remote viewers via TURN",
    ),
    SystemMode.DEGRADED: ModePolicy(
        streams_enabled=True,
        max_fps=15,
        max_bitrate_kbps=1500,
        require_turn=True,
        require_uplink=True,
        description="Reduced quality, recovering from transient faults",
    ),
    SystemMode.LOCAL_ONLY: ModePolicy(
        streams_enabled=True,
        max_fps=15,
        max_bitrate_kbps=2000,
        require_turn=False,
        require_uplink=False,
        description="No uplink/TURN — LAN viewers only",
    ),
    SystemMode.SAFE: ModePolicy(
        streams_enabled=False,
        max_fps=0,
        max_bitrate_kbps=0,
        require_turn=False,
        require_uplink=False,
        description="Control plane alive, all streaming disabled",
    ),
}


@dataclass
class _ModeState:
    """Thread-safe mutable mode state."""
    current: SystemMode = SystemMode.NOMINAL
    entered_at: float = field(default_factory=time.time)
    entered_at_mono: float = field(default_factory=time.monotonic)
    reason: str = "initial"
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    listeners: List[Callable[[SystemMode, SystemMode, str], None]] = field(
        default_factory=list, repr=False,
    )


_state = _ModeState()


def current_mode() -> SystemMode:
    """Return the current system operating mode."""
    with _state.lock:
        return _state.current


def mode_uptime_sec() -> float:
    """Seconds since the last mode transition."""
    with _state.lock:
        return time.monotonic() - _state.entered_at_mono


def current_policy() -> ModePolicy:
    """Return the policy for the current operating mode."""
    with _state.lock:
        return MODE_POLICIES[_state.current]


def mode_info() -> Dict[str, Any]:
    """Snapshot of current mode state (for API/diagnostics)."""
    with _state.lock:
        policy = MODE_POLICIES[_state.current]
        return {
            "mode": _state.current.value,
            "since": _state.entered_at,
            "uptime_s": round(time.monotonic() - _state.entered_at_mono, 1),
            "reason": _state.reason,
            "policy": {
                "streams_enabled": policy.streams_enabled,
                "max_fps": policy.max_fps,
                "max_bitrate_kbps": policy.max_bitrate_kbps,
                "require_turn": policy.require_turn,
                "require_uplink": policy.require_uplink,
            },
        }


def _post_transition(previous: SystemMode, target: SystemMode, reason: str, listeners: list) -> None:
    """Run side-effects after a state mutation (logging, metrics, callbacks)."""
    logger.warning(
        "MODE TRANSITION: %s → %s  reason=%s",
        previous.value, target.value, reason,
    )

    _ensure_metrics()
    if _system_mode_gauge is not None:
        _system_mode_gauge.set(target.level)
    if _mode_transitions_counter is not None:
        _mode_transitions_counter.labels(
            from_mode=previous.value, to_mode=target.value,
        ).inc()

    emit(
        domain=Domain.SYSTEM,
        severity=Severity.WARN if target.level > previous.level else Severity.INFO,
        detection_signal=reason,
        recovery_action=RecoveryAction.SWITCH_MODE,
        outcome=f"mode: {previous.value} → {target.value}",
        details={"from": previous.value, "to": target.value},
    )

    for cb in listeners:
        try:
            cb(previous, target, reason)
        except Exception:
            logger.exception(
                "mode listener %s failed during %s → %s",
                getattr(cb, "__name__", repr(cb)),
                previous.value, target.value,
            )


def transition(target: SystemMode, reason: str) -> bool:
    """
    Transition to *target* mode.

    Returns True if transition occurred, False if already in that mode.
    Emits an FDIR event for every transition.
    """
    with _state.lock:
        previous = _state.current
        if previous == target:
            return False

        _state.current = target
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = reason
        listeners = list(_state.listeners)

    _post_transition(previous, target, reason, listeners)
    return True


def degrade(reason: str) -> None:
    """Drop one level (NOMINAL→DEGRADED→LOCAL_ONLY→SAFE)."""
    with _state.lock:
        cur = _state.current
        nxt_level = min(cur.level + 1, SystemMode.SAFE.level)
        target = [m for m in SystemMode if m.level == nxt_level][0]
        if cur == target:
            return
        _state.current = target
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = reason
        listeners = list(_state.listeners)
    _post_transition(cur, target, reason, listeners)


def promote(target: SystemMode, reason: str) -> bool:
    """Promote to a better mode (only if target is better than current)."""
    with _state.lock:
        if target.level >= _state.current.level:
            return False
        previous = _state.current
        _state.current = target
        _state.entered_at = time.time()
        _state.entered_at_mono = time.monotonic()
        _state.reason = reason
        listeners = list(_state.listeners)
    _post_transition(previous, target, reason, listeners)
    return True


def on_transition(callback: Callable[[SystemMode, SystemMode, str], None]) -> None:
    """Register a listener for mode transitions."""
    with _state.lock:
        _state.listeners.append(callback)
