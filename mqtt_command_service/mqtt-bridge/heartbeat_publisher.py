"""HeartbeatPublisher: periodic system & connection status via MQTT."""

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class HeartbeatPublisher:
    """Periodic system & connection status publisher."""

    def __init__(
        self,
        interval: float,
        shutdown_event: threading.Event,
        publish_system_status: Callable[[], None],
        publish_connection_status: Callable[[], None],
    ) -> None:
        self._raw_interval = interval
        self._interval = max(1.0, interval) if interval > 0 else 0.0
        self._shutdown = shutdown_event
        self._publish_system_status = publish_system_status
        self._publish_connection_status = publish_connection_status
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._raw_interval <= 0:
            return
        if self._thread and self._thread.is_alive():
            return
        thread = threading.Thread(
            target=self._loop,
            name="mqtt-heartbeat",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        # Bridge sets _shutdown before calling stop(), so the loop will exit.
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=self._interval * 2)
        self._thread = None

    # Cap backoff so heartbeat interval never exceeds safety gate timeout.
    # With default 15s interval and 60s safety timeout, max wait = 30s (2x),
    # leaving sufficient margin for the safety gate to receive heartbeats.
    _MAX_BACKOFF_MULTIPLIER = 2

    def _loop(self) -> None:
        consecutive_failures = 0
        while not self._shutdown.is_set():
            try:
                self._publish_system_status()
                self._publish_connection_status()
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                if consecutive_failures <= 3 or consecutive_failures % 10 == 0:
                    logger.exception("[bridge] Heartbeat publishing failed.")
                else:
                    logger.warning(
                        "[bridge] Heartbeat publishing failed (%d consecutive failures, suppressing traceback).",
                        consecutive_failures,
                    )
            backoff = min(2 ** max(0, consecutive_failures - 1), self._MAX_BACKOFF_MULTIPLIER)
            wait_time = self._interval * backoff
            if self._shutdown.wait(wait_time):
                break
