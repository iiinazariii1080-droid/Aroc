import asyncio
import logging
import time
from typing import Any, Callable, Optional


class JoystickScheduler:
    """Rate-controller stage between ingress and joystick pipeline."""

    def __init__(
        self,
        frame_handler: Callable[[dict], bool],
        *,
        max_rate_hz: float = 25.0,
        queue_maxsize: int = 256,
    ) -> None:
        self._log = logging.getLogger(__name__)
        self._frame_handler = frame_handler
        self._min_interval = 1.0 / max(1.0, float(max_rate_hz))
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_task: Optional[asyncio.Task] = None
        self._last_dispatch_ts = 0.0

        # Metrics
        self.enqueued_total = 0
        self.dropped_total = 0
        self.processed_total = 0

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="joystick-scheduler-worker")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except Exception:
                pass

    def submit(self, frame: dict, *, source: str = "ingress") -> tuple[bool, Optional[str]]:
        envelope = {"frame": frame, "source": source, "submitted_ts": time.time()}
        try:
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                    self._queue.task_done()
                    self.dropped_total += 1
                    self._log.debug("joystick_scheduler: queue full, dropped oldest frame")
                except Exception:
                    pass
            self._queue.put_nowait(envelope)
            self.enqueued_total += 1
            return True, None
        except Exception as exc:
            self._log.warning("joystick_scheduler: submit failed: %s", exc)
            return False, "scheduler_queue_error"

    async def _worker_loop(self) -> None:
        self._log.info("joystick_scheduler: worker started")
        try:
            while True:
                envelope = await self._queue.get()
                try:
                    now = time.time()
                    delay = self._min_interval - (now - self._last_dispatch_ts)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self._last_dispatch_ts = time.time()
                    ok = self._frame_handler(envelope["frame"])
                    if not ok:
                        self._log.debug("joystick_scheduler: downstream rejected frame")
                except Exception as exc:
                    self._log.exception("joystick_scheduler: dispatch failed: %s", exc)
                finally:
                    self._queue.task_done()
                    self.processed_total += 1
        except asyncio.CancelledError:
            self._log.info("joystick_scheduler: worker cancelled")
        except Exception as exc:
            self._log.warning("joystick_scheduler: worker crashed: %s", exc)

    def queue_size(self) -> int:
        try:
            return self._queue.qsize()
        except Exception:
            return 0

    def is_running(self) -> bool:
        return bool(self._worker_task) and not self._worker_task.done()

