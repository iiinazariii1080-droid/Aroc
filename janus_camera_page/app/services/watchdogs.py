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

from app.core.settings import get_settings
from app.services import janus
from app.services.fdir_events import Domain, Severity, emit
from app.services.recovery_ladder import get_ladder
from app.services import system_mode

try:
    from app.routes.metrics import (
        watchdog_checks_total,
        watchdog_healthy_total,
        stream_active as stream_active_gauge,
        video_age_ms as video_age_gauge,
    )
    _HAS_METRICS = True
except Exception:  # pragma: no cover
    _HAS_METRICS = False

logger = logging.getLogger("watchdog")

# How many consecutive healthy checks before we reset the ladder
_NOMINAL_WINDOW_CHECKS = int(os.getenv("WATCHDOG_NOMINAL_CHECKS", "10"))

# Process-level start timestamp for grace period calculation
_STARTUP_TS = time.time()

# Monotonic timestamp of last Janus watchdog escalation — used for atomic
# dedup between the sync Janus thread and the async snapshot coroutine.
# Both read/write this via _lock to avoid the previous threading.Event race.
_escalation_lock = threading.Lock()
_last_janus_escalation_ts: float = 0.0
# Dedup window: snapshot watchdog skips if Janus escalated within this many seconds.
_ESCALATION_DEDUP_SEC = 5.0


def _in_grace_period() -> bool:
    """True while within the post-startup grace window."""
    return (time.time() - _STARTUP_TS) < get_settings().watchdog_grace_sec


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

    while True:
        try:
            if _HAS_METRICS:
                watchdog_checks_total.inc()

            summary = janus.janus_summary(settings.janus_mount_id)
            age = summary.get("video_age_ms")

            if _HAS_METRICS:
                video_age_gauge.set(age if isinstance(age, (int, float)) else -1)

            if age is not None and isinstance(age, (int, float)) and age <= settings.watchdog_stale_ms:
                # Stream is healthy
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
                else:
                    _mark_janus_escalated()
                    ladder.escalate(signal, Domain.PIPELINE)

        except Exception:
            healthy_streak = 0
            logger.exception("watchdog loop error")
            if not _in_grace_period():
                # Retry once after backoff before escalating on transient errors
                try:
                    time.sleep(_RETRY_BACKOFF_SEC)
                    janus.janus_summary(settings.janus_mount_id)
                    # Retry succeeded — transient error, don't escalate
                    logger.info("watchdog retry succeeded, skipping escalation")
                except Exception:
                    _mark_janus_escalated()
                    try:
                        ladder.escalate("watchdog_exception", Domain.JANUS)
                    except Exception:
                        logger.exception("ladder escalation failed")

        time.sleep(settings.watchdog_interval_sec)


def _mark_janus_escalated() -> None:
    """Record that Janus watchdog escalated in this cycle."""
    global _last_janus_escalation_ts
    with _escalation_lock:
        _last_janus_escalation_ts = time.monotonic()


def _janus_recently_escalated() -> bool:
    """True if Janus watchdog escalated within the dedup window."""
    with _escalation_lock:
        return (time.monotonic() - _last_janus_escalation_ts) < _ESCALATION_DEDUP_SEC


async def start_snapshot_watchdog() -> None:
    settings = get_settings()
    if not settings.snapshot_watchdog_enabled:
        return
    asyncio.create_task(_snapshot_watchdog_loop())


async def _snapshot_watchdog_loop() -> None:
    settings = get_settings()
    interval = max(1, settings.watchdog_interval_sec)
    ladder = get_ladder()

    while True:
        try:
            stat = os.stat(settings.snapshot_path)
            age_ms = int((time.time() - stat.st_mtime) * 1000)
            if age_ms > settings.watchdog_stale_ms:
                # Only escalate if janus watchdog did NOT already escalate recently
                if _in_grace_period():
                    pass  # grace period — skip
                elif _janus_recently_escalated():
                    logger.debug("snapshot stale (%dms) but janus watchdog already escalated", age_ms)
                else:
                    ladder.escalate(
                        f"snapshot_stale_ms={age_ms}",
                        Domain.SENSOR,
                    )
        except FileNotFoundError:
            if not _in_grace_period() and not _janus_recently_escalated():
                ladder.escalate("snapshot_missing", Domain.SENSOR)
        except Exception as exc:
            logger.error("snapshot watchdog error: %s", exc)
        await asyncio.sleep(interval)

