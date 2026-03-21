"""Server-side watchdogs with hierarchical FDIR recovery ladder.

Replaces the previous flat "restart on stale" approach with a
5-level escalating recovery:
    0. retry_handle  →  1. restart_pipeline  →  2. restart_janus
    →  3. usb_reset (depth only)  →  4. reboot_node

Grace period: for the first ``WATCHDOG_GRACE_SEC`` seconds after
startup, metrics are collected but escalation is suppressed — this
gives the camera pipeline time to initialise after a boot.

When the stream is healthy for ``WATCHDOG_NOMINAL_CHECKS`` consecutive
checks, the ladder resets to level 0 and the system mode is
promoted back to NOMINAL.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Optional

from app.core.settings import get_settings
from app.services import janus
from app.services.fdir_events import Domain, Severity, emit
from app.services.recovery_ladder import get_ladder
from app.services import system_mode

try:
    from app.metrics import (
        watchdog_checks_total,
        watchdog_healthy_total,
        stream_active as stream_active_gauge,
        video_age_ms as video_age_gauge,
        janus_reachable as janus_reachable_gauge,
    )
    _HAS_METRICS = True
except Exception:  # pragma: no cover
    _HAS_METRICS = False

logger = logging.getLogger("watchdog")

# How many consecutive healthy checks before we reset the ladder
_NOMINAL_WINDOW_CHECKS = int(os.getenv("WATCHDOG_NOMINAL_CHECKS", "10"))

# SAFE mode auto-reset: after this many seconds in SAFE mode, automatically
# reset the ladder and attempt recovery again.  Prevents permanent lockout
# when a transient condition (e.g. port conflict) resolves on its own.
_SAFE_MODE_AUTO_RESET_SEC = float(os.getenv("SAFE_MODE_AUTO_RESET_SEC", "300"))

# Process-level start timestamp for grace period calculation (monotonic —
# immune to NTP adjustments that can break the grace window on edge nodes).
_STARTUP_TS = time.monotonic()

# Set to True after first healthy frame detected — ends grace period early.
_first_healthy_seen = False

# Monotonic timestamp of last Janus watchdog escalation — used for atomic
# dedup between the sync Janus thread and the async snapshot coroutine.
# Both read/write this via _lock to avoid the previous threading.Event race.
_escalation_lock = threading.Lock()
_last_escalation_ts: float = 0.0
# Dedup window: any watchdog skips if another already escalated within this window.
_ESCALATION_DEDUP_SEC = 5.0

# Snapshot watchdog task handle — prevents GC from collecting the fire-and-forget task.
_snapshot_task: Optional[asyncio.Task] = None

# Tracks snapshot file missing state to avoid log spam (log once on transition).
_snapshot_missing_logged: bool = False

# DEF-02: NTP-immune snapshot freshness tracking.
# Instead of comparing wall clocks (time.time() vs st_mtime), we detect
# whether st_mtime has changed and measure elapsed time with monotonic clock.
_last_observed_mtime: float = 0.0
_last_mtime_change_mono: float = 0.0

# Stop signal for daemon threads — set during shutdown to break infinite loops.
_stop_event = threading.Event()


def _in_grace_period() -> bool:
    """True while within the post-startup grace window.

    Grace ends early once the first healthy frame is seen (so a dead-on-arrival
    pipeline is not masked for the full 60 s window).
    """
    with _escalation_lock:
        if _first_healthy_seen:
            return False
    return (time.monotonic() - _STARTUP_TS) < get_settings().watchdog_grace_sec


def start_janus_watchdog() -> None:
    settings = get_settings()
    if not settings.watchdog_enabled:
        return

    thread = threading.Thread(target=_watchdog_loop, daemon=True)
    thread.start()


def _watchdog_loop() -> None:
    settings = get_settings()
    ladder = get_ladder()
    healthy_streak = 0
    _RETRY_BACKOFF_SEC = 2

    while not _stop_event.is_set():
        try:
            if _HAS_METRICS:
                watchdog_checks_total.inc()

            summary = janus.janus_summary(settings.janus_mount_id)
            age = summary.get("video_age_ms")

            if _HAS_METRICS:
                janus_reachable_gauge.set(1)
                video_age_gauge.set(age if isinstance(age, (int, float)) else -1)

            if age is not None and isinstance(age, (int, float)) and age <= settings.watchdog_stale_ms:
                # Stream is healthy
                global _first_healthy_seen
                with _escalation_lock:
                    _first_healthy_seen = True
                healthy_streak += 1
                if _HAS_METRICS:
                    watchdog_healthy_total.inc()
                    stream_active_gauge.set(1)
                if healthy_streak >= _NOMINAL_WINDOW_CHECKS:
                    ladder.reset()
                    system_mode.promote(
                        system_mode.SystemMode.NOMINAL,
                        "stream healthy for sustained window",
                    )
                    healthy_streak = _NOMINAL_WINDOW_CHECKS  # cap
            else:
                # Stream is stale or absent → escalate (unless in grace period)
                healthy_streak = 0
                if _HAS_METRICS:
                    stream_active_gauge.set(0)
                signal = f"video_age_ms={age}" if age is not None else "video_age_ms=None"
                if _in_grace_period():
                    logger.info("watchdog: %s (grace period, skipping escalation)", signal)
                elif (
                    system_mode.current_mode() == system_mode.SystemMode.SAFE
                    and _SAFE_MODE_AUTO_RESET_SEC > 0
                    and system_mode.mode_uptime_sec() >= _SAFE_MODE_AUTO_RESET_SEC
                ):
                    # SAFE mode auto-reset: transient failures may have resolved.
                    # Reset ladder and try recovery again from level 0.
                    logger.warning(
                        "SAFE mode auto-reset after %.0fs — retrying recovery ladder",
                        system_mode.mode_uptime_sec(),
                    )
                    ladder.reset()
                    system_mode.promote(
                        system_mode.SystemMode.NOMINAL,
                        "safe_mode_auto_reset",
                    )
                else:
                    _try_escalate(ladder, signal, Domain.PIPELINE)

        except Exception:
            healthy_streak = 0
            if _HAS_METRICS:
                janus_reachable_gauge.set(0)
            logger.exception("watchdog loop error")
            if not _in_grace_period():
                # Retry once after backoff before escalating on transient errors
                try:
                    time.sleep(_RETRY_BACKOFF_SEC)
                    janus.janus_summary(settings.janus_mount_id)
                    # Retry succeeded — transient error, don't escalate
                    logger.info("watchdog retry succeeded, skipping escalation")
                except Exception:
                    try:
                        _try_escalate(ladder, "watchdog_exception", Domain.JANUS)
                    except Exception:
                        logger.exception("ladder escalation failed")

        _stop_event.wait(settings.watchdog_interval_sec)


def _try_escalate(ladder, signal: str, domain: Domain) -> bool:
    """Atomically claim the dedup window and escalate.

    Returns True if this watchdog won the window and escalated,
    False if another watchdog already escalated within the dedup window.
    This eliminates the check-then-act race between dual watchdogs.
    """
    global _last_escalation_ts
    with _escalation_lock:
        now = time.monotonic()
        if (now - _last_escalation_ts) < _ESCALATION_DEDUP_SEC:
            return False
        _last_escalation_ts = now
    # Escalate outside lock — ladder has its own internal lock.
    ladder.escalate(signal, domain)
    return True


def _recently_escalated() -> bool:
    """True if any watchdog escalated within the dedup window."""
    with _escalation_lock:
        return (time.monotonic() - _last_escalation_ts) < _ESCALATION_DEDUP_SEC


async def start_snapshot_watchdog() -> None:
    global _snapshot_task
    settings = get_settings()
    if not settings.snapshot_watchdog_enabled:
        return
    _snapshot_task = asyncio.create_task(_snapshot_watchdog_loop())


async def _snapshot_watchdog_loop() -> None:
    global _last_observed_mtime, _last_mtime_change_mono
    settings = get_settings()
    interval = max(1, settings.watchdog_interval_sec)
    ladder = get_ladder()

    while not _stop_event.is_set():
        try:
            stat_result = os.stat(settings.snapshot_path)
            now_mono = time.monotonic()

            global _snapshot_missing_logged
            if _snapshot_missing_logged:
                _snapshot_missing_logged = False
                logger.info("Snapshot file reappeared: %s", settings.snapshot_path)

            # DEF-02: NTP-immune age calculation.
            # Detect mtime change → record monotonic timestamp.
            # Measure staleness as monotonic elapsed since last mtime change.
            if stat_result.st_mtime != _last_observed_mtime:
                _last_observed_mtime = stat_result.st_mtime
                _last_mtime_change_mono = now_mono

            if _last_mtime_change_mono == 0.0:
                # First check — seed monotonic timestamp, skip this cycle.
                _last_mtime_change_mono = now_mono
            else:
                age_ms = int((now_mono - _last_mtime_change_mono) * 1000)
                if age_ms > settings.watchdog_stale_ms:
                    if _in_grace_period():
                        pass  # grace period — skip
                    elif not _try_escalate(ladder, f"snapshot_stale_ms={age_ms}", Domain.SENSOR):
                        logger.debug("snapshot stale (%dms) but dedup window active", age_ms)

        except FileNotFoundError:
            # Reset mtime tracking so detection works when file reappears.
            _last_observed_mtime = 0.0
            _last_mtime_change_mono = 0.0
            if not _in_grace_period():
                if not _snapshot_missing_logged:
                    _snapshot_missing_logged = True
                    logger.warning("Snapshot file missing: %s", settings.snapshot_path)
                _try_escalate(ladder, "snapshot_missing", Domain.SENSOR)
        except Exception as exc:
            logger.error("snapshot watchdog error: %s", exc)
        await asyncio.sleep(interval)


def stop_all() -> None:
    """Signal all watchdog loops to stop (called on app shutdown)."""
    global _last_observed_mtime, _last_mtime_change_mono
    _stop_event.set()
    _last_observed_mtime = 0.0
    _last_mtime_change_mono = 0.0
    if _snapshot_task is not None and not _snapshot_task.done():
        _snapshot_task.cancel()

