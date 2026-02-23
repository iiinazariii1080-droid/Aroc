"""GripperWatchdog: auto-release idle vacuum after configurable timeout.

Runs at ~1 Hz.  When the gripper is active longer than *timeout_s* **and**
the vacuum sensor does NOT report a held part, the watchdog triggers an
automatic GRIP_OPEN via *release_callback*.

If the vacuum sensor reports PART_GRIPPED (sdk value == 1) the timer is
extended — we never drop a held object.

When the SDK vacuum sensor is unavailable (degraded mode), the watchdog
falls back to a pure time-based release after timeout — it is safer to
release than to run the pump indefinitely without feedback.

References:
  ISO 10218-1 §5.4   — protective stop for auxiliary devices
  ISO/TS 15066        — continuous monitoring of end-effector state
  piCOBOTe datasheet  — duty-cycle limits for continuous vacuum
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Any, Optional

from fastapi.concurrency import run_in_threadpool

from app.metrics import inc_gripper_watchdog_releases

logger = logging.getLogger(__name__)


class GripperWatchdog:
    """Background task that auto-releases the vacuum gripper after idle timeout.

    Parameters
    ----------
    state_store_getter:
        Callable returning the ``StateStore`` instance.
    vacuum_reader:
        **Sync** callable that returns the SDK vacuum state:
        -1 = off, 0 = vacuum on / no part, 1 = part gripped, -99 = error.
    release_callback:
        **Async** callable invoked to deactivate the gripper (enqueue GRIP_OPEN).
    timeout_s:
        Max seconds the vacuum may stay active without a detected part.
    rate_hz:
        Check frequency (default 1 Hz — vacuum state is slow-changing).
    """

    def __init__(
        self,
        state_store_getter: Callable[[], Any],
        vacuum_reader: Callable[[], int],
        release_callback: Callable[[], Any],
        timeout_s: float = 120.0,
        rate_hz: float = 1.0,
    ):
        self._get_store = state_store_getter
        self._read_vacuum = vacuum_reader
        self._release = release_callback
        self._timeout_s = timeout_s
        self._interval = 1.0 / rate_hz if rate_hz > 0 else 1.0
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        # Tracks whether we already fired the release for the current
        # activation cycle (avoid spamming GRIP_OPEN commands).
        self._released_for_cycle: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._released_for_cycle = False
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "GripperWatchdog started: timeout=%.0fs, rate=%.1f Hz",
            self._timeout_s,
            1.0 / self._interval,
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("GripperWatchdog stopped")

    def reset(self) -> None:
        """Reset the one-shot flag so the watchdog can fire again.

        Called externally after a new GRIP_CLOSE succeeds.
        """
        self._released_for_cycle = False

    # ── Main loop ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._interval)
                store = self._get_store()
                snap = store.get_robot_snapshot()

                if not snap.gripper_active:
                    # Gripper is off — nothing to watch.
                    self._released_for_cycle = False
                    continue

                if self._released_for_cycle:
                    # Already released this cycle; wait for gripper to be
                    # turned on again (reset flag) before re-firing.
                    continue

                activated_at = snap.gripper_activated_at
                if activated_at is None:
                    # Active but no timestamp — legacy state before upgrade;
                    # treat as just activated.
                    continue

                elapsed = time.time() - activated_at
                if elapsed < self._timeout_s:
                    continue

                # ── Timeout exceeded — check vacuum sensor ───────────────
                try:
                    vacuum = await run_in_threadpool(self._read_vacuum)
                except Exception as exc:
                    logger.debug("GripperWatchdog vacuum read: %s", exc)
                    vacuum = -99  # treat sensor failure as "no part" (safe side)

                if vacuum == 1:
                    # Part is held → extend the timer by resetting activated_at.
                    logger.debug(
                        "GripperWatchdog: part detected (vacuum=1) after %.0fs — extending timer",
                        elapsed,
                    )
                    store.refresh_gripper_activated_at()
                    continue

                # vacuum ∈ {-1, 0, -99}: no part / off / sensor error → release
                logger.warning(
                    "GRIPPER_WATCHDOG_RELEASE: vacuum idle %.0fs (limit %.0fs), "
                    "vacuum_state=%d — auto-deactivating gripper",
                    elapsed,
                    self._timeout_s,
                    vacuum,
                )
                inc_gripper_watchdog_releases()
                self._released_for_cycle = True

                try:
                    await self._release()
                except Exception as exc:
                    logger.error("GripperWatchdog release callback failed: %s", exc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("GripperWatchdog tick: %s", exc)
