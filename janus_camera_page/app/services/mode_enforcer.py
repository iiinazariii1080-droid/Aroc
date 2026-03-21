"""Mode policy enforcement for rover-grade camera streaming.

Listens to system mode transitions and enforces the declared policies:
- SAFE mode: stop the camera pipeline service (safety-critical halt)
- DEGRADED / LOCAL_ONLY: write fps_profile signal for pipeline adjustment
- NOMINAL: ensure pipeline is running, clear fps_profile

Registered during app startup via ``register()``.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time

from app.core.settings import get_settings
from app.services.fdir_events import Domain, RecoveryAction, Severity, emit
from app.services.system import atomic_write_text
from app.services.system_mode import (
    MODE_POLICIES,
    SystemMode,
    on_transition,
)

logger = logging.getLogger("mode_enforcer")

_SYSTEMCTL_TIMEOUT = 30
_registered = False


def register() -> None:
    """Register the enforcer as a mode transition listener. Idempotent."""
    global _registered
    if _registered:
        return
    on_transition(_on_mode_transition)
    _registered = True
    logger.info("Mode enforcer registered")


def _on_mode_transition(
    previous: SystemMode, target: SystemMode, reason: str,
) -> None:
    """Callback fired on every mode transition — enforces the target policy."""
    policy = MODE_POLICIES[target]
    settings = get_settings()
    service = settings.service_name

    logger.warning(
        "ENFORCE mode=%s streams_enabled=%s max_fps=%d reason=%s",
        target.value, policy.streams_enabled, policy.max_fps, reason,
    )

    try:
        if target == SystemMode.SAFE:
            _stop_pipeline(service, reason)
        elif target == SystemMode.NOMINAL:
            _ensure_pipeline_running(service, reason)
            _clear_fps_profile()
        elif target in (SystemMode.DEGRADED, SystemMode.LOCAL_ONLY):
            _write_fps_profile(target, policy.max_fps, policy.max_bitrate_kbps)
            _ensure_pipeline_running(service, reason)
    except Exception:
        logger.exception("Mode enforcement failed for %s", target.value)

    _persist_history(previous, target, reason)


def _stop_pipeline(service: str, reason: str) -> None:
    """Stop the camera pipeline — SAFE mode critical halt."""
    if _is_service_active(service):
        logger.critical("SAFE MODE: stopping pipeline service %s", service)
        try:
            subprocess.run(
                ["sudo", "systemctl", "stop", service],
                timeout=_SYSTEMCTL_TIMEOUT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.critical(
                "systemctl stop %s failed (rc=%d): %s",
                service, exc.returncode, exc.stderr.decode(errors="replace").strip(),
            )
        except subprocess.TimeoutExpired:
            logger.critical("systemctl stop %s timed out after %ds", service, _SYSTEMCTL_TIMEOUT)

        # Verify the service actually stopped
        if _is_service_active(service):
            logger.critical(
                "SAFETY VIOLATION: pipeline %s still active after stop command", service,
            )
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=reason,
                recovery_action=RecoveryAction.RESTART_PIPELINE,
                outcome=f"pipeline stop FAILED — service still active: {service}",
            )
        else:
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=reason,
                recovery_action=RecoveryAction.RESTART_PIPELINE,
                outcome=f"pipeline stopped: {service}",
            )
    else:
        logger.info("Pipeline %s already stopped", service)


def _ensure_pipeline_running(service: str, reason: str) -> None:
    """Start the pipeline if it is not running."""
    if not _is_service_active(service):
        logger.warning("Starting pipeline service %s (mode requires streams)", service)
        try:
            subprocess.run(
                ["sudo", "systemctl", "start", service],
                timeout=_SYSTEMCTL_TIMEOUT,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "systemctl start %s failed (rc=%d): %s",
                service, exc.returncode, exc.stderr.decode(errors="replace").strip(),
            )
        except subprocess.TimeoutExpired:
            logger.error("systemctl start %s timed out", service)

        # Verify the service actually started
        if not _is_service_active(service):
            logger.error("Pipeline %s failed to start after start command", service)

        emit(
            domain=Domain.SYSTEM,
            severity=Severity.INFO,
            detection_signal=reason,
            recovery_action=RecoveryAction.RESTART_PIPELINE,
            outcome=f"pipeline started: {service}",
        )


def _is_service_active(service: str) -> bool:
    """Check if a systemd service is currently active."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "is-active", "--quiet", service],
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_fps_profile(mode: SystemMode, max_fps: int, max_bitrate_kbps: int) -> None:
    """Write the fps/bitrate profile signal file for the pipeline to read."""
    fps_profile_path = get_settings().fps_profile_path
    profile = "low" if mode == SystemMode.DEGRADED else "local"
    try:
        atomic_write_text(
            fps_profile_path,
            json.dumps({
                "profile": profile,
                "max_fps": max_fps,
                "max_bitrate_kbps": max_bitrate_kbps,
                "mode": mode.value,
                "ts": time.time(),
            }),
        )
        logger.info("fps_profile written: profile=%s fps=%d bitrate=%d", profile, max_fps, max_bitrate_kbps)
    except OSError:
        logger.exception("Failed to write fps_profile to %s", fps_profile_path)


def _clear_fps_profile() -> None:
    """Remove the fps_profile signal (return to defaults)."""
    fps_profile_path = get_settings().fps_profile_path
    try:
        if fps_profile_path.exists():
            fps_profile_path.unlink()
            logger.info("fps_profile cleared (nominal)")
    except OSError:
        logger.exception("Failed to clear fps_profile")


def _persist_history(previous: SystemMode, target: SystemMode, reason: str) -> None:
    """Append transition to mode history (ring buffer of last 50 entries)."""
    mode_history_path = get_settings().mode_history_path
    try:
        mode_history_path.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if mode_history_path.exists():
            try:
                history = json.loads(mode_history_path.read_text())
            except Exception:
                history = []
        history.append({
            "from": previous.value,
            "to": target.value,
            "reason": reason,
            "ts": time.time(),
        })
        # Keep last 50 entries
        history = history[-50:]
        atomic_write_text(mode_history_path, json.dumps(history, indent=1))
    except OSError:
        logger.debug("Failed to persist mode history")
