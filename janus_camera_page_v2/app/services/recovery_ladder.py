"""Hierarchical FDIR recovery ladder for camera streaming.

Implements the 5-level escalation required by deep-research-report
§Autonomy / ECSS-style FDIR:

    Level 0: retry / verify Janus + pipeline health
    Level 1: restart pipeline process (ffmpeg/realsense-mux)
    Level 2: restart Janus gateway
    Level 3: USB reset (for RealSense nodes — skipped on color)
    Level 4: reboot node (last resort, bounded by reboot counter)

Each level has a bounded attempt count and cooldown.  If a level
exhausts its budget, the ladder escalates to the next level.

Persistence:
  - Process-restart state: ``/run/camera/fdir_ladder.json`` (tmpfs)
  - Reboot-surviving state: ``/var/lib/camera-fdir/`` (reboot counter)

The reboot counter prevents infinite reboot loops: after MAX_FDIR_REBOOTS
FDIR-initiated reboots the ladder enters SAFE mode instead of rebooting again.

Every action is logged via fdir_events.emit().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import threading

# fcntl is Unix-specific; handle import gracefully for Windows dev/test
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.utils.fs import atomic_write_text
from app.utils.process import run_cmd
from app.services.fdir_events import (
    Domain,
    RecoveryAction,
    Severity,
    emit,
)
from app.services import system_mode

logger = logging.getLogger(__name__)

# ── Persistence paths (read lazily from Settings) ─────────────────────
# These are @property-like helpers so that tests can override the paths
# via environment variables without module reload.

def _ladder_state_path() -> Path:
    return get_settings().fdir_ladder_state


def _reboot_count_dir() -> Path:
    return get_settings().fdir_persist_dir


def _reboot_count_path() -> Path:
    return get_settings().fdir_persist_dir / "reboot_count"


def _reboot_marker_path() -> Path:
    return get_settings().fdir_persist_dir / "last_reboot_request"


# ── Ladder configuration ──────────────────────────────────────────────

@dataclass(frozen=True)
class LadderLevelConfig:
    """Immutable configuration for a single recovery level."""
    name: str
    action: RecoveryAction
    max_attempts: int
    cooldown_sec: float


# Backward-compatible alias used by tests and external consumers.


@dataclass
class LadderLevelState:
    """Mutable runtime state for a single recovery level."""
    attempts: int = 0
    last_attempt: float = 0.0


def _default_ladder() -> List[LadderLevelConfig]:
    """Create the default ladder, omitting usb_reset for color nodes."""
    settings = get_settings()
    levels: List[LadderLevelConfig] = [
        LadderLevelConfig(
            name="retry_handle",
            action=RecoveryAction.RETRY_HANDLE,
            max_attempts=1,
            cooldown_sec=10,
        ),
        LadderLevelConfig(
            name="restart_pipeline",
            action=RecoveryAction.RESTART_PIPELINE,
            max_attempts=5,
            cooldown_sec=45,
        ),
        LadderLevelConfig(
            name="restart_janus",
            action=RecoveryAction.RESTART_JANUS,
            max_attempts=3,
            cooldown_sec=90,
        ),
    ]
    # USB reset only applicable to depth camera nodes
    if settings.camera_type == "depth_camera":
        levels.append(LadderLevelConfig(
            name="usb_reset",
            action=RecoveryAction.USB_RESET,
            max_attempts=2,
            cooldown_sec=90,
        ))
    levels.append(LadderLevelConfig(
        name="reboot_node",
        action=RecoveryAction.REBOOT_NODE,
        max_attempts=1,
        cooldown_sec=300,
    ))
    return levels


# ── Persistence helpers ───────────────────────────────────────────────

def _read_reboot_count() -> int:
    try:
        return int(_reboot_count_path().read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_reboot_count(n: int) -> None:
    """Atomically write reboot count with file-level lock to prevent TOCTOU.

    Uses O_RDWR | O_CREAT (no O_TRUNC) so the file is not truncated before the
    exclusive lock is obtained.  Truncation happens inside the lock so concurrent
    writers cannot race on an already-emptied file.
    """
    try:
        _reboot_count_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_reboot_count_path()), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX)
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, (str(n) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("Cannot write reboot count: %s", exc)


def _atomic_increment_reboot_count() -> int:
    """Atomically read-increment-write reboot count. Returns the NEW count."""
    try:
        _reboot_count_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_reboot_count_path()), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 64).decode().strip()
            current = int(raw) if raw else 0
            new_val = current + 1
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, (str(new_val) + "\n").encode())
            os.fsync(fd)
            return new_val
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("Cannot increment reboot count: %s", exc)
        return _read_reboot_count() + 1


def _save_ladder_state(
    level: int,
    levels: List[LadderLevelConfig],
    state: Dict[str, LadderLevelState],
    total: int,
) -> None:
    """Atomically persist ladder state to tmpfs (survives process restart, not reboot)."""
    try:
        payload = {
            "level": level,
            "attempts": [state.get(lv.name, LadderLevelState()).attempts for lv in levels],
            "last_attempt": [state.get(lv.name, LadderLevelState()).last_attempt for lv in levels],
            "total_recoveries": total,
            "ts": time.time(),
        }
        atomic_write_text(_ladder_state_path(), json.dumps(payload))
    except OSError as exc:
        logger.warning("Cannot save ladder state: %s", exc)


def _load_ladder_state(
    levels: List[LadderLevelConfig],
) -> tuple[int, int, Dict[str, LadderLevelState]]:
    """Load ladder state from tmpfs.

    Returns (current_level, total_recoveries, state_dict).
    Does not mutate the level configs.
    """
    try:
        fd = os.open(str(_ladder_state_path()), os.O_RDONLY)
        try:
            if HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_SH)
            raw_bytes = os.read(fd, 8192)
        finally:
            os.close(fd)
        raw = json.loads(raw_bytes.decode())
        saved_level = int(raw.get("level", 0))
        total = int(raw.get("total_recoveries", 0))
        saved_attempts = raw.get("attempts", [])
        saved_last = raw.get("last_attempt", [])
        state_dict: Dict[str, LadderLevelState] = {}
        for i, lv in enumerate(levels):
            s = LadderLevelState()
            if i < len(saved_attempts):
                s.attempts = int(saved_attempts[i])
            if i < len(saved_last):
                s.last_attempt = float(saved_last[i])
            state_dict[lv.name] = s
        # Clamp level to valid range
        saved_level = max(0, min(saved_level, len(levels)))
        logger.info("Loaded ladder state from disk: level=%d total=%d", saved_level, total)
        return saved_level, total, state_dict
    except FileNotFoundError:
        return 0, 0, {}
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Corrupted ladder state file %s: %s — resetting to level 0", _ladder_state_path(), exc)
        emit(
            domain=Domain.SYSTEM,
            severity=Severity.WARN,
            detection_signal="ladder_state_corrupt",
            recovery_action=RecoveryAction.NONE,
            outcome=f"ladder state lost ({exc}), reset to level 0",
        )
        return 0, 0, {}


class RecoveryLadder:
    """
    Stateful escalating recovery controller with persistence.

    Call ``escalate(signal)`` when a fault is detected.
    Call ``reset()`` when the system returns to nominal.

    State is persisted to /run/camera/ (survives process restarts).
    A reboot counter in /var/lib/camera-fdir/ prevents infinite reboot loops.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._levels: List[LadderLevelConfig] = _default_ladder()
        self._current_level, self._total_recoveries, self._state = _load_ladder_state(self._levels)
        self._last_escalation_ts: float = 0.0

    def check_circuit_breaker(self) -> None:
        """Check reboot circuit breaker and transition to SAFE mode if tripped.

        Must be called explicitly from the application lifespan after startup,
        not from __init__, to avoid side-effecting global system mode during
        object construction or test setup.
        """
        settings = get_settings()
        reboots = _read_reboot_count()
        if reboots >= settings.max_fdir_reboots:
            with self._lock:
                self._current_level = len(self._levels)  # mark all levels exhausted
            self._trip_reboot_circuit_breaker(
                reboots, Domain.SYSTEM, f"fdir_reboots={reboots}", RecoveryAction.NONE
            )

    # ── Public API ────────────────────────────────────────────────

    async def escalate(self, detection_signal: str, domain: Domain = Domain.PIPELINE) -> Dict[str, Any]:
        """
        Attempt recovery at the current ladder level.

        If the level's budget is exhausted ⇒ escalate.
        Returns a dict describing what was done.
        Thread-safe: all state mutations are protected by _lock.

        Lock ordering note
        ------------------
        ``_execute()`` and **all** ``system_mode`` calls are made **outside**
        the lock.  ``_escalate_locked()`` only mutates ladder state and
        returns deferred side-effects (mode transitions, FDIR events) that
        ``escalate()`` executes after releasing the lock.  This prevents
        the deadlock chain ``RecoveryLadder._lock → _ModeState.lock →
        listener executor timeout``.
        """
        # Phase 1: escalation logic under lock — returns either a final result
        # or a deferred action that must run outside the lock.
        with self._lock:
            result_or_action = self._escalate_locked(detection_signal, domain)

        # Phase 2: execute deferred side-effects OUTSIDE the lock.
        if isinstance(result_or_action, dict):
            # Execute any deferred mode transitions and FDIR events that
            # _escalate_locked() could not run while holding the lock.
            for action_fn in result_or_action.pop("_deferred", []):
                action_fn()
            return result_or_action

        # Phase 3: _escalate_locked returned a tuple → run deferred + _execute().
        level, st, signal, dom, deferred_actions = result_or_action
        for action_fn in deferred_actions:
            action_fn()
        success = await self._execute(level, st, signal, dom)
        with self._lock:
            _save_ladder_state(self._current_level, self._levels, self._state, self._total_recoveries)
        return {
            "action": level.action.value,
            "level": level.name,
            "attempt": st.attempts,
            "max_attempts": level.max_attempts,
            "success": success,
        }

    def _escalate_locked(self, detection_signal: str, domain: Domain):
        """Determine what to do under the lock.

        Returns either:
        - A ``dict`` result (dedup/cooldown/exhausted — no action needed).
          May contain a ``_deferred`` list of callables that ``escalate()``
          must execute **after** releasing the lock (mode transitions,
          FDIR events).
        - A ``tuple(level, state, signal, domain)`` indicating that
          ``_execute()`` should be called **after** the lock is released.

        **Lock safety:** This method MUST NOT call ``system_mode.transition``,
        ``system_mode.degrade``, or ``emit()`` directly — these acquire
        their own locks and could deadlock.  Instead, append them to the
        ``_deferred`` list for post-lock execution.
        """
        # Collects side-effects to run after lock release.
        deferred: list = []

        # Iterative escalation — no recursion, safe for any ladder depth.
        while True:
            level = self._current_level_obj()
            if level is None:
                # All levels exhausted → defer SAFE mode transition
                _sig, _dom = detection_signal, domain
                deferred.append(
                    lambda: system_mode.transition(system_mode.SystemMode.SAFE, _sig)
                )
                deferred.append(lambda: emit(
                    domain=_dom,
                    severity=Severity.CRITICAL,
                    detection_signal=_sig,
                    recovery_action=RecoveryAction.NONE,
                    outcome="all recovery levels exhausted → SAFE mode",
                ))
                return {"action": "safe_mode", "reason": "ladder_exhausted", "_deferred": deferred}

            # Two time sources with different semantics:
            # - now_wall: wall-clock for level.last_attempt (persisted to disk,
            #   must survive process restarts — time.monotonic() resets at boot).
            # - now_mono: monotonic for _last_escalation_ts (in-process dedup
            #   only, immune to NTP jumps that could bypass the dedup window).
            now_wall = time.time()
            now_mono = time.monotonic()

            # Dedup: skip if another watchdog already escalated within the window
            if now_mono - self._last_escalation_ts < get_settings().fdir_dedup_sec:
                return {"action": "dedup_skip", "level": level.name}

            st = self._state.setdefault(level.name, LadderLevelState())

            # Cooldown check (wall-clock: persisted last_attempt must match)
            if now_wall - st.last_attempt < level.cooldown_sec:
                remaining = round(level.cooldown_sec - (now_wall - st.last_attempt), 1)
                return {"action": "cooldown", "remaining_sec": remaining, "level": level.name}

            # Budget exhausted → advance to next level and loop
            if st.attempts >= level.max_attempts:
                old_name = level.name
                self._current_level += 1
                _save_ladder_state(self._current_level, self._levels, self._state, self._total_recoveries)
                next_level = self._current_level_obj()
                if next_level is not None:
                    logger.warning(
                        "Escalating: %s → %s  (signal: %s)", old_name, next_level.name, detection_signal
                    )
                    from app.metrics.safe import safe_inc
                    safe_inc("watchdog_escalations_total", labels={"level": next_level.name})
                    # Defer emit and mode degrade to after lock release
                    _old, _next, _sig, _dom = old_name, next_level.name, detection_signal, domain
                    deferred.append(lambda: emit(
                        domain=_dom,
                        severity=Severity.WARN,
                        detection_signal=_sig,
                        recovery_action=RecoveryAction.NONE,
                        outcome=f"escalate: {_old} → {_next}",
                    ))
                    deferred.append(
                        lambda: system_mode.degrade(f"fdir_escalate:{_next}")
                    )
                continue  # re-evaluate the new level

            # Prepare state mutation — _execute() will run OUTSIDE the lock
            st.attempts += 1
            st.last_attempt = now_wall   # wall-clock: persisted across restarts
            self._total_recoveries += 1
            self._last_escalation_ts = now_mono  # monotonic: in-process dedup only

            from app.metrics.safe import safe_set
            safe_set("recovery_ladder_level", self._current_level)

            # Return a tuple to signal that escalate() should call _execute()
            # after releasing the lock.
            return (level, st, detection_signal, domain, deferred)

    def reset(self) -> None:
        """Reset ladder to level 0 (system recovered to nominal). Thread-safe.

        FDIR events are deferred until after the lock is released to maintain
        the same lock ordering discipline as ``escalate()``.
        """
        with self._lock:
            deferred = self._reset_locked()
        for fn in deferred:
            fn()

    def _reset_locked(self) -> list:
        deferred: list = []
        from app.metrics.safe import safe_set
        safe_set("recovery_ladder_level", 0)
        if self._current_level > 0:
            logger.info("Recovery ladder reset to level 0")
            _old_level = self._current_level
            deferred.append(lambda: emit(
                domain=Domain.SYSTEM,
                severity=Severity.INFO,
                detection_signal="system_nominal",
                recovery_action=RecoveryAction.NONE,
                outcome=f"ladder reset from level {_old_level}",
            ))
        self._current_level = 0
        self._state.clear()
        self._last_escalation_ts = 0.0
        _save_ladder_state(0, self._levels, self._state, self._total_recoveries)
        # Clear reboot counter — system is healthy again
        _write_reboot_count(0)
        return deferred

    def status(self) -> Dict[str, Any]:
        """Return ladder status for diagnostics. Thread-safe."""
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> Dict[str, Any]:
        return {
            "current_level": self._current_level,
            "current_level_name": self._current_level_obj().name if self._current_level_obj() else "exhausted",
            "total_recoveries": self._total_recoveries,
            "reboot_count": _read_reboot_count(),
            "max_fdir_reboots": get_settings().max_fdir_reboots,
            "levels": [
                {
                    "name": lvl.name,
                    "action": lvl.action.value,
                    "attempts": self._state.get(lvl.name, LadderLevelState()).attempts,
                    "max_attempts": lvl.max_attempts,
                    "cooldown_sec": lvl.cooldown_sec,
                }
                for lvl in self._levels
            ],
        }

    # ── Private ───────────────────────────────────────────────────

    def _trip_reboot_circuit_breaker(
        self,
        reboots: int,
        domain: Domain,
        signal: str,
        action: RecoveryAction,
    ) -> None:
        """Transition to SAFE mode and emit an event when the reboot circuit breaker trips."""
        settings = get_settings()
        logger.critical(
            "Reboot circuit breaker: %d FDIR reboots (max %d) — entering SAFE mode",
            reboots, settings.max_fdir_reboots,
        )
        system_mode.transition(
            system_mode.SystemMode.SAFE,
            f"reboot_circuit_breaker:{reboots}_reboots",
        )
        emit(
            domain=domain,
            severity=Severity.CRITICAL,
            detection_signal=signal,
            recovery_action=action,
            outcome=f"reboot circuit breaker tripped after {reboots} reboots → SAFE mode",
        )

    def _current_level_obj(self) -> Optional[LadderLevelConfig]:
        if self._current_level >= len(self._levels):
            return None
        return self._levels[self._current_level]

    async def _execute(self, level: LadderLevelConfig, state: LadderLevelState, signal: str, domain: Domain) -> bool:
        """Execute the actual recovery action (async). Returns success flag.

        Now runs directly on the event loop — no sync→async bridging needed.
        Blocking subprocess calls are dispatched via asyncio.to_thread().
        """
        settings = get_settings()
        action = level.action
        try:
            if action == RecoveryAction.RETRY_HANDLE:
                # Verify Janus is reachable — direct await, no bridging.
                from app.services import janus
                summary = await janus.janus_summary(settings.janus_mount_id)
                janus_ok = summary.get("reachable", False)
                # Also check if pipeline service is active (non-zero exit = inactive)
                try:
                    await asyncio.to_thread(
                        run_cmd,
                        ["sudo", "systemctl", "is-active", "--quiet", settings.service_name],
                        timeout=5,
                    )
                    pipeline_active = True
                except (RuntimeError, Exception):
                    pipeline_active = False
                outcome = f"handle_retry: janus_ok={janus_ok}, pipeline_active={pipeline_active}"
                if not janus_ok or not pipeline_active:
                    emit(
                        domain=domain,
                        severity=Severity.WARN,
                        detection_signal=signal,
                        recovery_action=action,
                        outcome=outcome,
                        details={"attempt": state.attempts, "level": level.name},
                    )
                    return False

            elif action == RecoveryAction.RESTART_PIPELINE:
                await asyncio.to_thread(
                    run_cmd, ["sudo", "systemctl", "restart", settings.service_name], timeout=45,
                )
                outcome = f"restarted {settings.service_name}"

            elif action == RecoveryAction.RESTART_JANUS:
                await asyncio.to_thread(
                    run_cmd, ["sudo", "systemctl", "restart", settings.janus_service_name], timeout=60,
                )
                outcome = f"restarted {settings.janus_service_name}"

            elif action == RecoveryAction.USB_RESET:
                await asyncio.to_thread(
                    run_cmd, ["sudo", "systemctl", "start", settings.realsense_failsafe_service_name], timeout=90,
                )
                outcome = f"usb_reset via {settings.realsense_failsafe_service_name}"

            elif action == RecoveryAction.REBOOT_NODE:
                if not settings.watchdog_reboot_enabled:
                    logger.warning("Reboot disabled by CAM_WATCHDOG_REBOOT_ENABLED=0 → SAFE mode")
                    system_mode.transition(
                        system_mode.SystemMode.SAFE,
                        "reboot_disabled_by_config",
                    )
                    emit(
                        domain=domain,
                        severity=Severity.CRITICAL,
                        detection_signal=signal,
                        recovery_action=action,
                        outcome="reboot skipped (disabled) → SAFE mode",
                    )
                    return False

                # Reboot circuit breaker
                reboots = _read_reboot_count()
                if reboots >= settings.max_fdir_reboots:
                    self._trip_reboot_circuit_breaker(reboots, domain, signal, action)
                    return False

                # Write reboot marker + increment counter
                _atomic_increment_reboot_count()
                try:
                    _reboot_marker_path().write_text(
                        json.dumps({"ts": time.time(), "signal": signal}) + "\n"
                    )
                except OSError:
                    pass

                emit(
                    domain=domain,
                    severity=Severity.CRITICAL,
                    detection_signal=signal,
                    recovery_action=action,
                    outcome=f"initiating node reboot (count={reboots + 1})",
                )
                await asyncio.to_thread(
                    run_cmd, ["sudo", "systemctl", "reboot"], timeout=10,
                )
                outcome = "reboot initiated"

            else:
                outcome = f"unknown action: {action}"

            emit(
                domain=domain,
                severity=Severity.WARN,
                detection_signal=signal,
                recovery_action=action,
                outcome=outcome,
                details={"attempt": state.attempts, "level": level.name},
            )
            return True

        except Exception as exc:
            emit(
                domain=domain,
                severity=Severity.ERROR,
                detection_signal=signal,
                recovery_action=action,
                outcome=f"FAILED: {exc}",
                details={"attempt": state.attempts, "level": level.name},
            )
            logger.exception("Recovery action %s failed", action.value)
            return False


# ── Module-level singleton (lazy — avoids import-time filesystem I/O) ──
_ladder: Optional[RecoveryLadder] = None
_ladder_init_lock = threading.Lock()


def get_ladder() -> RecoveryLadder:
    global _ladder
    if _ladder is None:
        with _ladder_init_lock:
            if _ladder is None:
                _ladder = RecoveryLadder()
    return _ladder


def _reset_for_tests() -> None:
    """Reset module-level singleton for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    global _ladder
    _ladder = None
