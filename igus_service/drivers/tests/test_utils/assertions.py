"""Dynamic assertion patterns for testing asynchronous state changes.

Provides Eventually, Always, and debounced_predicate for testing dynamic
signals that change over time (statusword, position, etc.).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


class AssertionTimeoutError(AssertionError):
    """Raised when an Eventually assertion times out."""

    pass


class AssertionViolationError(AssertionError):
    """Raised when an Always assertion is violated."""

    pass


async def Eventually(
    condition: Callable[[], Awaitable[bool]] | Callable[[], bool],
    timeout_s: float,
    poll_interval_s: float = 0.05,
    error_message: str | None = None,
) -> None:
    """Assert that a condition becomes true within a timeout.

    Polls the condition at regular intervals until it becomes true or
    the timeout is reached.

    Args:
        condition: Async or sync callable that returns bool
        timeout_s: Maximum time to wait (seconds)
        poll_interval_s: Interval between polls (seconds)
        error_message: Optional custom error message

    Raises:
        AssertionTimeoutError: If condition never becomes true within timeout
    """
    deadline = asyncio.get_event_loop().time() + timeout_s

    while True:
        if asyncio.iscoroutinefunction(condition):
            result = await condition()
        else:
            result = condition()

        if result:
            return

        if asyncio.get_event_loop().time() >= deadline:
            msg = (
                error_message
                or f"Condition did not become true within {timeout_s}s"
            )
            raise AssertionTimeoutError(msg)

        await asyncio.sleep(poll_interval_s)


async def Always(
    condition: Callable[[], Awaitable[bool]] | Callable[[], bool],
    duration_s: float,
    poll_interval_s: float = 0.05,
    error_message: str | None = None,
) -> None:
    """Assert that a condition remains true for a duration.

    Continuously checks the condition for the specified duration.
    If it becomes false at any point, raises AssertionViolationError.

    Args:
        condition: Async or sync callable that returns bool
        duration_s: Duration to maintain condition (seconds)
        poll_interval_s: Interval between checks (seconds)
        error_message: Optional custom error message

    Raises:
        AssertionViolationError: If condition becomes false during duration
    """
    deadline = asyncio.get_event_loop().time() + duration_s

    while True:
        if asyncio.iscoroutinefunction(condition):
            result = await condition()
        else:
            result = condition()

        if not result:
            msg = (
                error_message
                or f"Condition became false before {duration_s}s elapsed"
            )
            raise AssertionViolationError(msg)

        if asyncio.get_event_loop().time() >= deadline:
            return

        await asyncio.sleep(poll_interval_s)


async def debounced_predicate(
    condition: Callable[[], Awaitable[bool]] | Callable[[], bool],
    n_consecutive: int,
    poll_interval_s: float = 0.05,
) -> bool:
    """Check if a condition is confirmed N times consecutively.

    This is useful for filtering out glitches in statusword bits
    (e.g., Target reached, Switched on, Warning).

    Args:
        condition: Async or sync callable that returns bool
        n_consecutive: Number of consecutive confirmations required
        poll_interval_s: Interval between polls (seconds)

    Returns:
        True if condition was true for N consecutive polls, False otherwise
    """
    consecutive_count = 0

    while consecutive_count < n_consecutive:
        if asyncio.iscoroutinefunction(condition):
            result = await condition()
        else:
            result = condition()

        if result:
            consecutive_count += 1
        else:
            consecutive_count = 0  # Reset on any false

        if consecutive_count < n_consecutive:
            await asyncio.sleep(poll_interval_s)

    return True

