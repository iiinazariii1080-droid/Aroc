"""System Operating Modes for rover-grade camera streaming.

Implements explicit system modes as required by deep-research-report
§Autonomy: "Define explicit system modes (Nominal / Degraded /
Local-only / Safe).  Each mode has: which streams are published,
bitrate/FPS caps, which dependencies are required, exit criteria
back to nominal."

Modes form a lattice:
    NOMINAL  →  DEGRADED  →  LOCAL_ONLY  →  SAFE
    (any transition back requires explicit promotion)

FPS enforcement note
--------------------
``ModePolicy.max_fps`` and ``max_bitrate_kbps`` are advisory values
exposed via the diagnostic API for operator visibility.  Actual FPS
enforcement happens at the streaming pipeline level via the fps_profile
file (``/run/camera/fps_profile``).

``fps_profile`` is the authoritative field: every mode transition writes
it so the pipeline stays synchronised regardless of whether the transition
was triggered by the FDIR ladder or the thermal monitor.

    NOMINAL     → "normal"
    DEGRADED    → "low"
    LOCAL_ONLY  → "low"
    SAFE        → "stop"

Lock ordering convention
------------------------
``_ModeState.lock`` is a **leaf lock** — code holding it must never acquire
another lock (RecoveryLadder._lock, fdir_events._persist_lock, etc.).
Callers (thermal thread, watchdog, FDIR ladder) must release their own
locks *before* calling ``transition()`` / ``degrade()`` / ``promote()``.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List

from app.core.settings import get_settings
from app.services.fdir_events import Domain, RecoveryAction, Severity, emit

logger = logging.getLogger(__name__)

# Lazy metric imports — avoids circular import at module load time.
# Protected by _metrics_init_lock to prevent races when multiple threads
# call _ensure_metrics() concurrently (e.g. parallel mode transitions).
_metrics_loaded = False
_system_mode_gauge = None
_mode_transitions_counter = None
_metrics_init_lock = threading.Lock()


def _ensure_metrics():  # noqa: D401
    global _metrics_loaded, _system_mode_gauge, _mode_transitions_counter
    if _metrics_loaded:
        return
    with _metrics_init_lock:
        if _metrics_loaded:
            return
        try:
            from app.metrics import system_mode as _g, mode_transitions_total as _c
            _system_mode_gauge = _g
            _mode_transitions_counter = _c
        except Exception:  # ImportError if prometheus_client absent
            pass
        _metrics_loaded = True


# Module-level executor — shared across all transitions so we don't spin up
# a new thread pool for every listener on every mode change.
_LISTENER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="mode_listener"
)
# Set to True after shutdown_listener_executor() so that daemon threads still
# running after lifespan teardown don't submit to an already-closed executor.
_executor_stopped: bool = False


def shutdown_listener_executor() -> None:
    """Shut down the listener thread pool.  Called from the lifespan shutdown."""
    global _executor_stopped
    _executor_stopped = True
    _LISTENER_EXECUTOR.shutdown(wait=False, cancel_futures=True)


class SystemMode(str, Enum):
    """Operating modes ordered by degradation level."""
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    LOCAL_ONLY = "local_only"
    SAFE = "safe"

    @property
    def level(self) -> int:
        return _MODE_LEVELS[self]


# Defined immediately after the class so _MODE_LEVELS is always fully populated
# before any caller can reference SystemMode.level — no deferred initialization.
_MODE_LEVELS: Dict[SystemMode, int] = {
    SystemMode.NOMINAL: 0,
    SystemMode.DEGRADED: 1,
    SystemMode.LOCAL_ONLY: 2,
    SystemMode.SAFE: 3,
}
_LEVEL_TO_MODE: Dict[int, SystemMode] = {v: k for k, v in _MODE_LEVELS.items()}


@dataclass
class ModePolicy:
    """Policy per mode.

    ``max_fps`` and ``max_bitrate_kbps`` are advisory/informational values
    exposed via the diagnostic API.  Actual stream-quality enforcement is done
    by the streaming pipeline reading fps_profile from
    ``/run/camera/fps_profile``.

    ``fps_profile`` is the authoritative field: mode transitions write it to
    the profile file so the pipeline stays synchronised with the mode state.
    """
    streams_enabled: bool = True
    max_fps: int = 30
    max_bitrate_kbps: int = 4000
    require_turn: bool = True
    require_uplink: bool = True
    fps_profile: str = "normal"   # "normal" | "low" | "stop"
    description: str = ""


# ── Default policies per mode ─────────────────────────────────────────
MODE_POLICIES: Dict[SystemMode, ModePolicy] = {
    SystemMode.NOMINAL: ModePolicy(
        streams_enabled=True,
        max_fps=30,
        max_bitrate_kbps=4000,
        require_turn=True,
        require_uplink=True,
        fps_profile="normal",
        description="All streams active, remote viewers via TURN",
    ),
    SystemMode.DEGRADED: ModePolicy(
        streams_enabled=True,
        max_fps=15,
        max_bitrate_kbps=1500,
        require_turn=True,
        require_uplink=True,
        fps_profile="low",
        description="Reduced quality, recovering from transient faults",
    ),
    SystemMode.LOCAL_ONLY: ModePolicy(
        streams_enabled=True,
        max_fps=15,
        max_bitrate_kbps=2000,
        require_turn=False,
        require_uplink=False,
        fps_profile="low",
        description="No uplink/TURN — LAN viewers only",
    ),
    SystemMode.SAFE: ModePolicy(
        streams_enabled=False,
        max_fps=0,
        max_bitrate_kbps=0,
        require_turn=False,
        require_uplink=False,
        fps_profile="stop",
        description="Control plane alive, all streaming disabled",
    ),
}

@dataclass
class _ModeState:
    """Thread-safe mutable mode state."""
    current: SystemMode = SystemMode.NOMINAL
    entered_at: float = field(default_factory=time.time)
    reason: str = "initial"
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    listeners: List[Callable[[SystemMode, SystemMode, str], None]] = field(
        default_factory=list, repr=False,
    )


_state = _ModeState()


def current_mode() -> SystemMode:
    """Return the current system operating mode (thread-safe)."""
    with _state.lock:
        return _state.current


def current_policy() -> ModePolicy:
    """Return the policy for the current operating mode (thread-safe)."""
    with _state.lock:
        return MODE_POLICIES[_state.current]


def mode_info() -> Dict[str, Any]:
    """Snapshot of current mode state (for API/diagnostics)."""
    with _state.lock:
        policy = MODE_POLICIES[_state.current]
        return {
            "mode": _state.current.value,
            "since": _state.entered_at,
            "uptime_s": round(time.time() - _state.entered_at, 1),
            "reason": _state.reason,
            "policy": {
                "streams_enabled": policy.streams_enabled,
                "max_fps": policy.max_fps,
                "max_bitrate_kbps": policy.max_bitrate_kbps,
                "require_turn": policy.require_turn,
                "require_uplink": policy.require_uplink,
                "fps_profile": policy.fps_profile,
                "fps_note": (
                    "max_fps/max_bitrate are advisory; "
                    "fps_profile is the authoritative pipeline control"
                ),
            },
        }


def _apply_fps_profile(profile: str) -> None:
    """Write fps_profile to the pipeline control file (best-effort, non-fatal).

    Failure is logged as ERROR and emitted as an FDIR event so operators
    know the pipeline was NOT updated even though the mode transition
    succeeded.  Without this, the system could report SAFE mode while the
    stream continues running.
    """
    try:
        from app.services.thermal import set_fps_profile
        set_fps_profile(profile)
    except Exception:
        logger.error(
            "Failed to apply fps_profile=%s — pipeline may be out of sync with mode",
            profile,
            exc_info=True,
        )
        emit(
            domain=Domain.SYSTEM,
            severity=Severity.ERROR,
            detection_signal=f"fps_profile_write_failed={profile}",
            recovery_action=RecoveryAction.NONE,
            outcome="mode transitioned but pipeline fps_profile NOT updated",
        )


def _post_transition(previous: SystemMode, target: SystemMode, reason: str, listeners: list) -> None:
    """Run side-effects after a state mutation (logging, metrics, fps_profile, callbacks)."""
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

    # Synchronise the streaming pipeline's fps_profile with the new mode
    # BEFORE running listener callbacks, so listeners always observe a
    # consistent state where fps_profile matches the new mode.
    _apply_fps_profile(MODE_POLICIES[target].fps_profile)

    emit(
        domain=Domain.SYSTEM,
        severity=Severity.WARN if target.level > previous.level else Severity.INFO,
        detection_signal=reason,
        recovery_action=RecoveryAction.SWITCH_MODE,
        outcome=f"mode: {previous.value} → {target.value}",
        details={"from": previous.value, "to": target.value},
    )

    listener_timeout = get_settings().mode_listener_timeout_sec
    for cb in listeners:
        if _executor_stopped:
            # Executor already shut down — no-op remaining callbacks.
            break
        try:
            future = _LISTENER_EXECUTOR.submit(cb, previous, target, reason)
            future.result(timeout=listener_timeout)
        except concurrent.futures.TimeoutError:
            logger.error(
                "mode listener %s timed out after %.1fs during %s → %s",
                getattr(cb, "__name__", repr(cb)),
                listener_timeout,
                previous.value, target.value,
            )
        except RuntimeError:
            # Executor was shut down concurrently (daemon thread race).
            break
        except Exception:
            logger.exception("mode listener error")


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
        _state.reason = reason
        listeners = list(_state.listeners)

    _post_transition(previous, target, reason, listeners)
    return True


def degrade(reason: str) -> None:
    """Drop one level (NOMINAL→DEGRADED→LOCAL_ONLY→SAFE)."""
    with _state.lock:
        cur = _state.current
        nxt_level = min(cur.level + 1, SystemMode.SAFE.level)
        target = _LEVEL_TO_MODE[nxt_level]
        if cur == target:
            return
        _state.current = target
        _state.entered_at = time.time()
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
        _state.reason = reason
        listeners = list(_state.listeners)
    _post_transition(previous, target, reason, listeners)
    return True


def on_transition(callback: Callable[[SystemMode, SystemMode, str], None]) -> None:
    """Register a listener for mode transitions. Idempotent — duplicate registrations are ignored.

    Currently test-only: no production code registers listeners.
    The listener executor (``_LISTENER_EXECUTOR``) and callback mechanism
    are retained for extensibility and test coverage.
    """
    with _state.lock:
        if callback not in _state.listeners:
            _state.listeners.append(callback)


def _reset_for_tests() -> None:
    """Reset module-level singletons for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    global _state, _metrics_loaded, _system_mode_gauge, _mode_transitions_counter
    _state = _ModeState()
    _metrics_loaded = False
    _system_mode_gauge = None
    _mode_transitions_counter = None
