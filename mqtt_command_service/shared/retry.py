"""Unified retry helper with exponential backoff and jitter."""

import logging
import random
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


def retry_with_backoff[T](
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    on_retry: Callable[[int, Exception], None] | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Execute *fn* with exponential backoff and optional jitter.

    Args:
        fn: Callable to retry on exception.
        max_retries: Maximum number of attempts (total, not retries).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: If True, add random jitter to prevent thundering herd.
        on_retry: Optional callback(attempt, exception) called before each retry sleep.
        retryable_exceptions: Exception types to retry on. Non-matching exceptions
            propagate immediately. Defaults to (Exception,) for backward compatibility.

    Returns:
        The return value of *fn* on success.

    Raises:
        The last exception if all retries are exhausted, or immediately
        if the exception is not in retryable_exceptions.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= 0.5 + random.random()
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]
