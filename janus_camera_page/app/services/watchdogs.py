from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

from app.core.settings import get_settings
from app.services import janus
from app.services.system import service_restart

_last_restart = 0.0


def start_janus_watchdog() -> None:
    settings = get_settings()
    if not settings.watchdog_enabled:
        return

    thread = threading.Thread(target=_watchdog_loop, daemon=True)
    thread.start()


def _watchdog_loop() -> None:
    global _last_restart
    settings = get_settings()
    while True:
        try:
            summary = janus.janus_summary(settings.janus_mount_id)
            age = summary.get("video_age_ms")
            if age is None or (isinstance(age, (int, float)) and age > settings.watchdog_stale_ms):
                now = time.time()
                if now - _last_restart > 60:
                    service_restart()
                    _last_restart = now
        except Exception:
            logging.exception("watchdog loop error")
        time.sleep(settings.watchdog_interval_sec)


async def start_snapshot_watchdog() -> None:
    settings = get_settings()
    if not settings.snapshot_watchdog_enabled:
        return
    asyncio.create_task(_snapshot_watchdog_loop())

async def _snapshot_watchdog_loop() -> None:
    settings = get_settings()
    interval = max(1, settings.watchdog_interval_sec)
    while True:
        try:
            stat = os.stat(settings.snapshot_path)
            age_ms = int((time.time() - stat.st_mtime) * 1000)
            if age_ms > settings.watchdog_stale_ms:
                service_restart()
        except FileNotFoundError:
            try:
                service_restart()
            except Exception as exc:
                logging.error("snapshot watchdog restart error: %s", exc)
        except Exception as exc:
            logging.error("snapshot watchdog error: %s", exc)
        await asyncio.sleep(interval)

