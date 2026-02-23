import asyncio
import logging
import time
from typing import Any, Callable, Optional


class JoystickIngress:
    """Ingress queue that decouples API sources from the joystick pipeline."""

    def __init__(
        self,
        submit_callback: Callable[[dict], bool],
        *,
        queue_maxsize: int = 512,
    ) -> None:
        self._log = logging.getLogger(__name__)
        self._submit_callback = submit_callback
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_task: Optional[asyncio.Task] = None

        # Metrics
        self.enqueued_total = 0
        self.dropped_total = 0
        self.processed_total = 0

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="joystick-ingress-worker")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except Exception:
                pass

    def submit(self, frame: dict, *, source: str = "http") -> tuple[bool, Optional[str]]:
        """Submit frame envelope into ingress queue."""
        envelope = {
            "frame": frame,
            "source": source,
            "received_ts": time.time(),
        }
        try:
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                    self._queue.task_done()
                    self.dropped_total += 1
                    self._log.debug("joystick_ingress: queue full, dropped oldest envelope")
                except Exception:
                    pass
            self._queue.put_nowait(envelope)
            self.enqueued_total += 1
            return True, None
        except Exception as exc:
            self._log.warning("joystick_ingress: submit failed: %s", exc)
            return False, "ingress_queue_error"

    async def _worker_loop(self) -> None:
        self._log.info("joystick_ingress: worker started")
        try:
            while True:
                envelope = await self._queue.get()
                try:
                    frame = envelope["frame"]
                    ok = self._submit_callback(frame)
                    if not ok:
                        self.dropped_total += 1
                        self._log.debug("joystick_ingress: downstream rejected frame")
                except Exception as exc:
                    self._log.exception("joystick_ingress: forwarding error: %s", exc)
                    self.dropped_total += 1
                finally:
                    self._queue.task_done()
                    self.processed_total += 1
        except asyncio.CancelledError:
            self._log.info("joystick_ingress: worker cancelled")
        except Exception as exc:
            self._log.warning("joystick_ingress: worker crashed: %s", exc)

    def queue_size(self) -> int:
        try:
            return self._queue.qsize()
        except Exception:
            return 0

    def is_running(self) -> bool:
        return bool(self._worker_task) and not self._worker_task.done()

