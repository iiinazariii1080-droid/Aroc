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

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.services.fdir_events import (
    Domain,
    RecoveryAction,
    Severity,
    emit,
)
from app.services import system_mode

logger = logging.getLogger("fdir.ladder")

# ── Persistence paths ─────────────────────────────────────────────────
_LADDER_STATE_PATH = Path(os.getenv(
    "FDIR_LADDER_STATE", "/run/camera/fdir_ladder.json",
))
_REBOOT_COUNT_DIR = Path(os.getenv(
    "FDIR_PERSIST_DIR", "/var/lib/camera-fdir",
))
_REBOOT_COUNT_PATH = _REBOOT_COUNT_DIR / "reboot_count"
_REBOOT_MARKER_PATH = _REBOOT_COUNT_DIR / "last_reboot_request"

# Dedup: minimum seconds between escalation calls that consume an attempt
_DEDUP_WINDOW_SEC = float(os.getenv("FDIR_DEDUP_SEC", "3"))


# ── Ladder configuration ──────────────────────────────────────────────

@dataclass
class LadderLevel:
    """Configuration for a single recovery level."""
    name: str
    action: RecoveryAction
    max_attempts: int
    cooldown_sec: float
    # runtime state
    attempts: int = 0
    last_attempt: float = 0.0


def _default_ladder() -> List[LadderLevel]:
    """Create the default ladder, omitting usb_reset for color nodes."""
    settings = get_settings()
    levels: List[LadderLevel] = [
        LadderLevel(
            name="retry_handle",
            action=RecoveryAction.RETRY_HANDLE,
            max_attempts=1,
            cooldown_sec=10,
        ),
        LadderLevel(
            name="restart_pipeline",
            action=RecoveryAction.RESTART_PIPELINE,
            max_attempts=5,
            cooldown_sec=45,
        ),
        LadderLevel(
            name="restart_janus",
            action=RecoveryAction.RESTART_JANUS,
            max_attempts=3,
            cooldown_sec=90,
        ),
    ]
    # USB reset only applicable to depth camera nodes
    if settings.camera_type == "depth_camera":
        levels.append(LadderLevel(
            name="usb_reset",
            action=RecoveryAction.USB_RESET,
            max_attempts=2,
            cooldown_sec=90,
        ))
    levels.append(LadderLevel(
        name="reboot_node",
        action=RecoveryAction.REBOOT_NODE,
        max_attempts=1,
        cooldown_sec=300,
    ))
    return levels


# ── Persistence helpers ───────────────────────────────────────────────

def _read_reboot_count() -> int:
    try:
        return int(_REBOOT_COUNT_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_reboot_count(n: int) -> None:
    try:
        _REBOOT_COUNT_DIR.mkdir(parents=True, exist_ok=True)
        _REBOOT_COUNT_PATH.write_text(str(n) + "\n")
    except OSError as exc:
        logger.warning("Cannot write reboot count: %s", exc)


def _save_ladder_state(level: int, levels: List[LadderLevel], total: int) -> None:
    """Persist ladder state to tmpfs (survives process restart, not reboot)."""
    try:
        _LADDER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "level": level,
            "attempts": [lv.attempts for lv in levels],
            "last_attempt": [lv.last_attempt for lv in levels],
            "total_recoveries": total,
            "ts": time.time(),
        }
        _LADDER_STATE_PATH.write_text(json.dumps(state))
    except OSError as exc:
        logger.warning("Cannot save ladder state: %s", exc)


def _load_ladder_state(levels: List[LadderLevel]) -> tuple[int, int]:
    """Load ladder state from tmpfs.  Returns (current_level, total_recoveries)."""
    try:
        raw = json.loads(_LADDER_STATE_PATH.read_text())
        saved_level = int(raw.get("level", 0))
        total = int(raw.get("total_recoveries", 0))
        saved_attempts = raw.get("attempts", [])
        saved_last = raw.get("last_attempt", [])
        for i, lv in enumerate(levels):
            if i < len(saved_attempts):
                lv.attempts = int(saved_attempts[i])
            if i < len(saved_last):
                lv.last_attempt = float(saved_last[i])
        # Clamp level to valid range
        saved_level = max(0, min(saved_level, len(levels)))
        logger.info("Loaded ladder state from disk: level=%d total=%d", saved_level, total)
        return saved_level, total
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError):
        return 0, 0


class RecoveryLadder:
    """
    Stateful escalating recovery controller with persistence.

    Call ``escalate(signal)`` when a fault is detected.
    Call ``reset()`` when the system returns to nominal.

    State is persisted to /run/camera/ (survives process restarts).
    A reboot counter in /var/lib/camera-fdir/ prevents infinite reboot loops.
    """

    def __init__(self) -> None:
        self._levels = _default_ladder()
        self._current_level, self._total_recoveries = _load_ladder_state(self._levels)
        self._last_escalation_ts: float = 0.0

        # Check reboot circuit breaker
        settings = get_settings()
        reboots = _read_reboot_count()
        if reboots >= settings.max_fdir_reboots:
            logger.critical(
                "Reboot circuit breaker: %d FDIR reboots (max %d) — entering SAFE mode",
                reboots, settings.max_fdir_reboots,
            )
            self._current_level = len(self._levels)  # mark all levels exhausted
            system_mode.transition(
                system_mode.SystemMode.SAFE,
                f"reboot_circuit_breaker:{reboots}_reboots",
            )
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=f"fdir_reboots={reboots}",
                recovery_action=RecoveryAction.NONE,
                outcome=f"reboot circuit breaker tripped after {reboots} reboots → SAFE mode",
            )

    # ── Public API ────────────────────────────────────────────────

    def escalate(self, detection_signal: str, domain: Domain = Domain.PIPELINE) -> Dict[str, Any]:
        """
        Attempt recovery at the current ladder level.

        If the level's budget is exhausted ⇒ escalate.
        Returns a dict describing what was done.
        """
        level = self._current_level_obj()
        if level is None:
            # All levels exhausted → SAFE mode
            system_mode.transition(system_mode.SystemMode.SAFE, detection_signal)
            emit(
                domain=domain,
                severity=Severity.CRITICAL,
                detection_signal=detection_signal,
                recovery_action=RecoveryAction.NONE,
                outcome="all recovery levels exhausted → SAFE mode",
            )
            return {"action": "safe_mode", "reason": "ladder_exhausted"}

        now = time.time()

        # Dedup: skip if another watchdog already escalated within the window
        if now - self._last_escalation_ts < _DEDUP_WINDOW_SEC:
            return {"action": "dedup_skip", "level": level.name}

        # Cooldown check
        if now - level.last_attempt < level.cooldown_sec:
            remaining = round(level.cooldown_sec - (now - level.last_attempt), 1)
            return {"action": "cooldown", "remaining_sec": remaining, "level": level.name}

        # Budget check → escalate if exhausted
        if level.attempts >= level.max_attempts:
            return self._escalate_to_next(detection_signal, domain)

        # Execute recovery action
        level.attempts += 1
        level.last_attempt = now
        self._total_recoveries += 1
        self._last_escalation_ts = now

        # Prometheus gauge
        try:
            from app.routes.metrics import recovery_ladder_level
            recovery_ladder_level.set(self._current_level)
        except Exception:
            pass

        success = self._execute(level, detection_signal, domain)
        _save_ladder_state(self._current_level, self._levels, self._total_recoveries)

        return {
            "action": level.action.value,
            "level": level.name,
            "attempt": level.attempts,
            "max_attempts": level.max_attempts,
            "success": success,
        }

    def reset(self) -> None:
        """Reset ladder to level 0 (system recovered to nominal)."""
        try:
            from app.routes.metrics import recovery_ladder_level
            recovery_ladder_level.set(0)
        except Exception:
            pass
        if self._current_level > 0:
            logger.info("Recovery ladder reset to level 0")
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.INFO,
                detection_signal="system_nominal",
                recovery_action=RecoveryAction.NONE,
                outcome=f"ladder reset from level {self._current_level}",
            )
        self._current_level = 0
        for lvl in self._levels:
            lvl.attempts = 0
            lvl.last_attempt = 0.0
        self._last_escalation_ts = 0.0
        _save_ladder_state(0, self._levels, self._total_recoveries)
        # Clear reboot counter — system is healthy again
        _write_reboot_count(0)

    def status(self) -> Dict[str, Any]:
        """Return ladder status for diagnostics."""
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
                    "attempts": lvl.attempts,
                    "max_attempts": lvl.max_attempts,
                    "cooldown_sec": lvl.cooldown_sec,
                }
                for lvl in self._levels
            ],
        }

    # ── Private ───────────────────────────────────────────────────

    def _current_level_obj(self) -> Optional[LadderLevel]:
        if self._current_level >= len(self._levels):
            return None
        return self._levels[self._current_level]

    def _escalate_to_next(self, signal: str, domain: Domain) -> Dict[str, Any]:
        old_name = self._levels[self._current_level].name
        self._current_level += 1
        _save_ladder_state(self._current_level, self._levels, self._total_recoveries)
        if self._current_level < len(self._levels):
            new_name = self._levels[self._current_level].name
            logger.warning("Escalating: %s → %s  (signal: %s)", old_name, new_name, signal)
            # Prometheus escalation counter
            try:
                from app.routes.metrics import watchdog_escalations_total
                watchdog_escalations_total.labels(level=new_name).inc()
            except Exception:
                pass
            emit(
                domain=domain,
                severity=Severity.WARN,
                detection_signal=signal,
                recovery_action=RecoveryAction.NONE,
                outcome=f"escalate: {old_name} → {new_name}",
            )
            # Degrade system mode on each escalation
            system_mode.degrade(f"fdir_escalate:{new_name}")
            return self.escalate(signal, domain)  # immediately try next level
        else:
            return self.escalate(signal, domain)  # will hit exhausted branch

    def _execute(self, level: LadderLevel, signal: str, domain: Domain) -> bool:
        """Execute the actual recovery action. Returns success flag."""
        settings = get_settings()
        action = level.action
        try:
            if action == RecoveryAction.RETRY_HANDLE:
                # Verify Janus is reachable AND check pipeline service status
                from app.services import janus
                janus.janus_summary(settings.janus_mount_id)
                # Also check if pipeline service is active
                try:
                    result = subprocess.run(
                        ["sudo", "systemctl", "is-active", "--quiet", settings.service_name],
                        timeout=5,
                    )
                    pipeline_active = result.returncode == 0
                except Exception:
                    pipeline_active = False
                outcome = f"handle_retry: janus_ok, pipeline_active={pipeline_active}"

            elif action == RecoveryAction.RESTART_PIPELINE:
                _run_cmd(["sudo", "systemctl", "restart", settings.service_name], timeout=15)
                outcome = f"restarted {settings.service_name}"

            elif action == RecoveryAction.RESTART_JANUS:
                _run_cmd(["sudo", "systemctl", "restart", "janus.service"], timeout=20)
                outcome = "restarted janus.service"

            elif action == RecoveryAction.USB_RESET:
                _run_cmd(["sudo", "systemctl", "start", "realsense-failsafe.service"], timeout=90)
                outcome = "usb_reset via realsense-failsafe"

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
                    return True

                # Reboot circuit breaker
                reboots = _read_reboot_count()
                if reboots >= settings.max_fdir_reboots:
                    logger.critical("Reboot circuit breaker: %d reboots → SAFE mode", reboots)
                    system_mode.transition(
                        system_mode.SystemMode.SAFE,
                        f"reboot_circuit_breaker:{reboots}",
                    )
                    emit(
                        domain=domain,
                        severity=Severity.CRITICAL,
                        detection_signal=signal,
                        recovery_action=action,
                        outcome=f"reboot blocked (circuit breaker: {reboots} reboots) → SAFE mode",
                    )
                    return True

                # Write reboot marker + increment counter
                _write_reboot_count(reboots + 1)
                try:
                    _REBOOT_MARKER_PATH.write_text(
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
                _run_cmd(["sudo", "systemctl", "reboot"], timeout=10)
                outcome = "reboot initiated"

            else:
                outcome = f"unknown action: {action}"

            emit(
                domain=domain,
                severity=Severity.WARN,
                detection_signal=signal,
                recovery_action=action,
                outcome=outcome,
                details={"attempt": level.attempts, "level": level.name},
            )
            return True

        except Exception as exc:
            emit(
                domain=domain,
                severity=Severity.ERROR,
                detection_signal=signal,
                recovery_action=action,
                outcome=f"FAILED: {exc}",
                details={"attempt": level.attempts, "level": level.name},
            )
            logger.exception("Recovery action %s failed", action.value)
            return False


def _run_cmd(cmd: list[str], timeout: int = 10) -> str:
    """Run a shell command; raise on failure."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exit={result.returncode}: {result.stderr.strip()}")
    return result.stdout


# ── Module-level singleton ────────────────────────────────────────────
_ladder = RecoveryLadder()


def get_ladder() -> RecoveryLadder:
    return _ladder
