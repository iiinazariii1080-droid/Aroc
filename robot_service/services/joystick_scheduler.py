"""Joystick scheduler (rate limiter) with *latest-only* semantics.

Senior teleop / jog controllers do **not** buffer joystick frames.
They keep only the latest input state and apply it at a fixed control rate.

Why:
  - If a buffer exists and the system can't keep up (CPU, logging, network),
    old frames ("button is pressed") get processed after release, causing
    2-4s "ghost" motion.
  - For jog, deterministic behaviour is: latest frame wins + TTL/watchdog.

This scheduler:
  - Accepts frames at any rate.
  - Stores ONLY the latest frame (overwriting previous -> counted as dropped).
  - Dispatches at <= max_rate_hz.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional


_LOGGER = logging.getLogger(__name__)


@dataclass
class SchedulerStats:
    submitted_total: int = 0
    dispatched_total: int = 0
    dropped_total: int = 0
    last_dispatch_mono: float = 0.0

    @property
    def queue_size(self) -> int:
        # semantics: either we have a pending latest frame or not
        return 1


class JoystickScheduler:
    """Rate-limits joystick frames while keeping only the latest frame."""

    def __init__(
        self,
        frame_handler: Callable[[dict], Awaitable[bool]],
        *,
        max_rate_hz: float = 25.0,
    ) -> None:
        self._frame_handler = frame_handler
        self._max_rate_hz = max(1.0, float(max_rate_hz))
        self._min_interval_s = 1.0 / self._max_rate_hz

        self._latest: Optional[dict] = None
        self._lock = asyncio.Lock()
        self._has_frame = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._running = False

        self.stats = SchedulerStats()

    def is_running(self) -> bool:
        return self._running

    def queue_size(self) -> int:
        return 1 if self._latest is not None else 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker_loop(), name="joystick_scheduler")
        _LOGGER.info("[sched] started max_rate_hz=%.1f", self._max_rate_hz)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _LOGGER.info(
            "[sched] stopped submitted=%d dispatched=%d dropped=%d",
            self.stats.submitted_total,
            self.stats.dispatched_total,
            self.stats.dropped_total,
        )

    def submit(self, frame: dict, *, source: str = "unknown") -> bool:
        """Submit a joystick frame (latest overwrites previous)."""
        if not self._running:
            return False

        self.stats.submitted_total += 1

        # Copy & attach local receipt clocks (authoritative for TTL)
        f = dict(frame)
        f.setdefault("_source", source)
        f.setdefault("_rx_time", time.time())
        f.setdefault("_rx_mono", time.monotonic())

        # Overwrite latest frame (counts as drop of pending frame)
        async def _store() -> None:
            async with self._lock:
                if self._latest is not None:
                    self.stats.dropped_total += 1
                self._latest = f
                self._has_frame.set()

        # submit() can be called from sync FastAPI handlers; schedule on loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_store())
        except RuntimeError:
            # Shouldn't happen in normal asyncio app, but keep safe fallback
            return False

        return True

    async def _worker_loop(self) -> None:
        last_dispatch_mono = 0.0
        while self._running:
            # Wait until we have at least one frame
            await self._has_frame.wait()

            # Rate limit
            now_mono = time.monotonic()
            dt = now_mono - last_dispatch_mono
            if dt < self._min_interval_s:
                await asyncio.sleep(self._min_interval_s - dt)

            # Consume the latest frame (latest-wins)
            async with self._lock:
                frame = self._latest
                self._latest = None
                # Clear; new submit() will set again
                self._has_frame.clear()

            if frame is None:
                continue

            # Compute local age (monotonic)
            now_mono = time.monotonic()
            rx_mono = float(frame.get("_rx_mono", now_mono))
            frame["_age_ms"] = (now_mono - rx_mono) * 1000.0

            ok = False
            try:
                ok = await self._frame_handler(frame)
            except Exception:
                _LOGGER.exception("[sched] frame_handler failed")

            if ok:
                self.stats.dispatched_total += 1

            last_dispatch_mono = time.monotonic()
            self.stats.last_dispatch_mono = last_dispatch_mono
