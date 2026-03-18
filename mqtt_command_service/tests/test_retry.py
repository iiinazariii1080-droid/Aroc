"""Tests for retry_with_backoff — retryable_exceptions filtering."""

import pytest

from shared.retry import retry_with_backoff


class TestRetryableExceptions:
    def test_default_retries_all_exceptions(self):
        """Default (Exception,) retries any exception type."""
        calls = 0

        def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ValueError("bad")
            return "ok"

        result = retry_with_backoff(flaky, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert calls == 3

    def test_non_retryable_propagates_immediately(self):
        """ValueError propagates immediately when not in retryable_exceptions."""
        calls = 0

        def always_fails():
            nonlocal calls
            calls += 1
            raise ValueError("bad data")

        with pytest.raises(ValueError, match="bad data"):
            retry_with_backoff(
                always_fails,
                max_retries=5,
                base_delay=0.01,
                retryable_exceptions=(ConnectionError, TimeoutError),
            )
        assert calls == 1  # No retries — propagated immediately

    def test_retryable_exception_is_retried(self):
        """ConnectionError is retried when in retryable_exceptions."""
        calls = 0

        def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("refused")
            return "connected"

        result = retry_with_backoff(
            flaky,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        assert result == "connected"
        assert calls == 3

    def test_exhausted_retries_raises_last(self):
        """After max_retries, the last retryable exception is raised."""
        def always_fails():
            raise ConnectionError("still down")

        with pytest.raises(ConnectionError, match="still down"):
            retry_with_backoff(
                always_fails,
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(ConnectionError,),
            )

    def test_on_retry_callback_fires(self):
        """on_retry callback fires for retryable exceptions."""
        retries = []

        def flaky():
            if len(retries) < 1:
                raise ConnectionError("fail")
            return "ok"

        def on_retry(attempt, exc):
            retries.append((attempt, str(exc)))

        retry_with_backoff(
            flaky,
            max_retries=3,
            base_delay=0.01,
            on_retry=on_retry,
            retryable_exceptions=(ConnectionError,),
        )
        assert len(retries) == 1
        assert retries[0][0] == 1
