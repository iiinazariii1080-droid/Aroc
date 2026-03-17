"""Server-side watchdogs with hierarchical FDIR recovery ladder.

Implements a 5-level escalating recovery:
    0. retry_handle  →  1. restart_pipeline  →  2. restart_janus
    →  3. usb_reset (depth only)  →  4. reboot_node

Both the Janus watchdog and the snapshot watchdog run as async Tasks
on the main event loop.  ``ladder.escalate()`` is fully async —
blocking subprocess calls inside the ladder are dispatched via
``asyncio.to_thread()`` internally.

``system_mode.promote`` is called directly (sub-millisecond: lock
acquire + state check + file write).  The threading.Lock it uses
is shared with the thermal daemon thread but never held long enough
to block the event loop.

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

from app.metrics.safe import safe_inc, safe_set

logger = logging.getLogger(__name__)

# Process-level start timestamp for grace period calculation.
# Set to 0.0 until start_janus_watchdog() is called so that the grace period
# begins at actual service startup, not at import time.
_STARTUP_TS: float = 0.0

# Monotonic timestamp of last Janus watchdog escalation — used for atomic
# dedup between the Janus task and the snapshot task.
# Both read/write this via _escalation_lock for thread-safety (to_thread
# dispatches ladder.escalate into a worker thread).
_escalation_lock = threading.Lock()
_last_janus_escalation_ts: float = 0.0
# Dedup window: snapshot watchdog skips if Janus escalated within this many seconds.
_ESCALATION_DEDUP_SEC = 5.0


def _in_grace_period() -> bool:
    """True while within the post-startup grace window."""
    return (time.time() - _STARTUP_TS) < get_settings().watchdog_grace_sec


_janus_watchdog_task: asyncio.Task | None = None


async def start_janus_watchdog() -> None:
    """Start the Janus stream watchdog as an async Task.

    Must be called from within a running event loop (e.g. the FastAPI
    lifespan).  The event loop reference is stored for sync callers
    that need to bridge back via ``run_coroutine_threadsafe()``.
    """
    global _STARTUP_TS, _janus_watchdog_task
    _STARTUP_TS = time.time()
    settings = get_settings()
    if not settings.watchdog_enabled:
        return

    _janus_watchdog_task = asyncio.create_task(_watchdog_loop())


async def stop_janus_watchdog() -> None:
    """Cancel the Janus watchdog task and wait for it to finish.

    If the task is mid-escalation (blocking in ``asyncio.to_thread``),
    the cancellation waits for the thread to complete before returning.
    """
    global _janus_watchdog_task
    task = _janus_watchdog_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _janus_watchdog_task = None


async def _watchdog_loop() -> None:
    """Main Janus watchdog loop (runs as async Task on the event loop).

    Directly awaits ``janus.janus_summary()`` — no thread→async bridge
    needed.  Blocking recovery actions are dispatched via
    ``asyncio.to_thread()`` so the event loop stays responsive.
    """
    # Settings are read inside the loop (not cached before it) so that
    # tests using get_settings.cache_clear() or env overrides take effect.
    # This matches _thermal_loop()'s strategy and prevents stale-config bugs.
    ladder = get_ladder()
    healthy_streak = 0
    retry_wait_sec = 2  # fixed wait before single retry (not a backoff)

    while True:
        settings = get_settings()
        try:
            safe_set("watchdog_heartbeat", time.time())
            safe_inc("watchdog_checks_total")

            summary = await janus.janus_summary(settings.janus_mount_id)
            safe_set("janus_reachable", 1 if summary.get("reachable", False) else 0)
            age = summary.get("video_age_ms")

            safe_set("video_age_ms", age if isinstance(age, (int, float)) else -1)

            if janus.is_stream_fresh(age, settings.watchdog_stale_ms):
                # Stream is healthy
                healthy_streak += 1
                safe_inc("watchdog_healthy_total")
                safe_set("stream_active", 1)
                if healthy_streak >= settings.watchdog_nominal_checks:
                    ladder.reset()
                    system_mode.promote(
                        system_mode.SystemMode.NOMINAL,
                        "stream healthy for sustained window",
                    )
                    healthy_streak = settings.watchdog_nominal_checks  # cap
            else:
                # Stream is stale or absent → escalate (unless in grace period)
                healthy_streak = 0
                safe_set("stream_active", 0)
                signal = f"video_age_ms={age}" if age is not None else "video_age_ms=None"
                # Distinguish: Janus unreachable (network/app fault) vs RTP pipeline stale
                escalation_domain = (
                    Domain.JANUS if not summary.get("reachable", False) else Domain.PIPELINE
                )
                if _in_grace_period():
                    logger.info("watchdog: %s (grace period, skipping escalation)", signal)
                else:
                    _mark_janus_escalated()
                    await ladder.escalate(signal, escalation_domain)

        except asyncio.CancelledError:
            raise  # allow task cancellation to propagate
        except Exception:
            healthy_streak = 0
            safe_set("janus_reachable", 0)
            logger.exception("watchdog loop error")
            if not _in_grace_period():
                # Retry once after brief wait before escalating on transient errors
                try:
                    await asyncio.sleep(retry_wait_sec)
                except asyncio.CancelledError:
                    raise
                try:
                    await janus.janus_summary(settings.janus_mount_id)
                    # Retry succeeded — transient error, don't escalate
                    logger.info("watchdog retry succeeded, skipping escalation")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _mark_janus_escalated()
                    try:
                        await ladder.escalate("watchdog_exception", Domain.JANUS)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("ladder escalation failed")

        try:
            await asyncio.sleep(settings.watchdog_interval_sec)
        except asyncio.CancelledError:
            raise


def _mark_janus_escalated() -> None:
    """Record that Janus watchdog escalated in this cycle."""
    global _last_janus_escalation_ts
    with _escalation_lock:
        _last_janus_escalation_ts = time.monotonic()


def _janus_recently_escalated() -> bool:
    """True if Janus watchdog escalated within the dedup window."""
    with _escalation_lock:
        return (time.monotonic() - _last_janus_escalation_ts) < _ESCALATION_DEDUP_SEC


_snapshot_task: asyncio.Task | None = None


async def start_snapshot_watchdog() -> None:
    global _snapshot_task
    settings = get_settings()
    if not settings.snapshot_watchdog_enabled:
        return
    _snapshot_task = asyncio.create_task(_snapshot_watchdog_loop())


async def stop_snapshot_watchdog() -> None:
    global _snapshot_task
    if _snapshot_task and not _snapshot_task.done():
        _snapshot_task.cancel()
        try:
            await _snapshot_task
        except asyncio.CancelledError:
            pass
        _snapshot_task = None


def _reset_for_tests() -> None:
    """Reset module-level state for test isolation.

    Called by ``ServiceRegistry.reset()`` — keeps internal details private.
    """
    global _janus_watchdog_task, _last_janus_escalation_ts, _STARTUP_TS
    _janus_watchdog_task = None
    _last_janus_escalation_ts = 0.0
    _STARTUP_TS = 0.0


async def _snapshot_watchdog_loop() -> None:
    ladder = get_ladder()
    # Backoff counter for consecutive FileNotFoundError — prevents rapid
    # escalation when the snapshot file never appears (e.g. pipeline not started).
    _consecutive_missing = 0
    _MAX_MISSING_BACKOFF = 5  # escalate at most every N * interval seconds

    while True:
        # Read settings per-iteration (same strategy as _watchdog_loop) so
        # that tests using get_settings.cache_clear() or env overrides work.
        settings = get_settings()
        interval = max(1, settings.watchdog_interval_sec)
        try:
            stat = os.stat(settings.snapshot_path)
            _consecutive_missing = 0  # file exists — reset backoff
            age_ms = int((time.time() - stat.st_mtime) * 1000)
            if age_ms > settings.watchdog_stale_ms:
                # Only escalate if janus watchdog did NOT already escalate recently.
                # escalate() may invoke blocking subprocess calls —
                # run it in a thread to keep the event loop free.
                if _in_grace_period():
                    pass  # grace period — skip
                elif _janus_recently_escalated():
                    logger.debug("snapshot stale (%dms) but janus watchdog already escalated", age_ms)
                else:
                    await ladder.escalate(
                        f"snapshot_stale_ms={age_ms}",
                        Domain.SENSOR,
                    )
        except FileNotFoundError:
            _consecutive_missing += 1
            if not _in_grace_period() and not _janus_recently_escalated():
                # Backoff: only escalate on the first miss and then every
                # _MAX_MISSING_BACKOFF intervals to avoid exhausting the
                # recovery ladder when the pipeline simply hasn't started yet.
                if _consecutive_missing == 1 or _consecutive_missing % _MAX_MISSING_BACKOFF == 0:
                    await ladder.escalate(
                        "snapshot_missing",
                        Domain.SENSOR,
                    )
                else:
                    logger.debug(
                        "snapshot missing (count=%d), backoff — next escalation at count=%d",
                        _consecutive_missing,
                        (_consecutive_missing // _MAX_MISSING_BACKOFF + 1) * _MAX_MISSING_BACKOFF,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("snapshot watchdog error: %s", exc)
        await asyncio.sleep(interval)
