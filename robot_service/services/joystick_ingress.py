"""Joystick ingress.

This component MUST NOT buffer joystick frames.

Rationale (teleop best-practice):
  - For jog control, the only frame that matters is the **latest** one.
  - Any buffering/backlog will replay old "button still pressed" frames after the
    user has already released the control, causing motion lag (seconds).
  - Staleness must be evaluated using **local** time (receipt/monotonic), never
    remote timestamps.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Tuple


class JoystickIngress:
    """Thin ingress that annotates frames with local receive timestamps.

    No internal queue, no worker tasks. Backpressure and rate limiting are
    handled by the downstream scheduler.
    """

    def __init__(
        self,
        submit_callback: Callable[[Dict], bool],
    ) -> None:
        self._submit_callback = submit_callback
        self._running = False

        # Metrics
        self.received_total = 0
        self.submitted_total = 0
        self.dropped_total = 0

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def queue_size(self) -> int:
        return 0  # no internal queue — pass-through only

    def submit(self, frame: Dict, *, source: str = "unknown") -> Tuple[bool, Optional[str]]:
        """Submit a joystick frame.

        Returns (ok, error).
        """
        self.received_total += 1

        if not self._running:
            self.dropped_total += 1
            return False, "ingress_not_running"

        # Local timestamps are the only trustworthy basis for TTL.
        # Keep remote `ts` (if provided) for debugging only.
        rx_mono = time.monotonic()
        rx_time = time.time()

        payload = dict(frame)
        payload["_rx_mono"] = rx_mono
        payload["_rx_time"] = rx_time

        ok = self._submit_callback(payload)
        if ok:
            self.submitted_total += 1
            return True, None

        self.dropped_total += 1
        return False, "scheduler_rejected"
