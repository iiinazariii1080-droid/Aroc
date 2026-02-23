"""Monotonic time helpers.

Why:
- Avoid wall-clock jumps (NTP adjustments) affecting deadlines and timeouts.
- Keep all internal scheduling expressed in monotonic seconds/milliseconds.
"""

from __future__ import annotations

import time


def monotonic_s() -> float:
    """Return monotonic time in seconds."""
    return time.monotonic()


def monotonic_ms() -> int:
    """Return monotonic time in integer milliseconds."""
    return int(time.monotonic() * 1000)


def sleep_s(seconds: float) -> None:
    """Sleep for the given duration (seconds)."""
    if seconds <= 0:
        return
    time.sleep(seconds)


class Deadline:
    """A simple deadline helper based on monotonic time."""

    __slots__ = ("_t_end",)

    def __init__(self, timeout_s: float | None) -> None:
        self._t_end = None if timeout_s is None else (monotonic_s() + float(timeout_s))

    @property
    def expired(self) -> bool:
        if self._t_end is None:
            return False
        return monotonic_s() >= self._t_end

    def remaining(self) -> float | None:
        if self._t_end is None:
            return None
        return max(0.0, self._t_end - monotonic_s())
