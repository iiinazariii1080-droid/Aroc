"""Retry/backoff utilities for transport operations.

We separate:
- RetryPolicy: configuration of backoff and jitter
- RetryBudget: state for an individual retry loop (attempt counter, deadlines)

Transport typically retries only *I/O and connection* errors.
Protocol validation errors should generally not be retried (they indicate a bug or corrupted framing).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .clock import monotonic_s, sleep_s


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configurable retry policy with exponential backoff + jitter."""

    max_attempts: int = 3
    base_delay_s: float = 0.10
    backoff_factor: float = 2.0
    max_delay_s: float = 1.0
    jitter_fraction: float = 0.20  # +/- 20%
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError, OSError)

    def delay_for_attempt(self, attempt: int) -> float:
        """Compute delay before the next attempt (attempt starts at 1)."""
        if attempt <= 1:
            delay = self.base_delay_s
        else:
            delay = self.base_delay_s * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_delay_s)

        if self.jitter_fraction > 0:
            jitter = delay * self.jitter_fraction
            delay = random.uniform(max(0.0, delay - jitter), delay + jitter)
        return delay


@dataclass(slots=True)
class RetryBudget:
    """Mutable state for retry loops."""

    policy: RetryPolicy
    deadline_s: float | None = None
    attempts: int = 0

    def can_retry(self) -> bool:
        if self.attempts >= self.policy.max_attempts:
            return False
        if self.deadline_s is None:
            return True
        return monotonic_s() < self.deadline_s

    def sleep_before_next(self) -> None:
        delay = self.policy.delay_for_attempt(self.attempts + 1)
        if self.deadline_s is not None:
            remaining = self.deadline_s - monotonic_s()
            if remaining <= 0:
                return
            delay = min(delay, remaining)
        sleep_s(delay)
