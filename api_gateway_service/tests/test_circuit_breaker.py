"""Tests for CircuitBreaker (app/core/circuit_breaker.py).

Covers CLOSED → OPEN → HALF_OPEN state transitions, recovery timing,
success/failure counting, registry, and describe().
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.core.circuit_breaker import CircuitBreaker, get_breaker, all_breakers, _breakers, _State


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear global breaker registry between tests."""
    _breakers.clear()
    yield
    _breakers.clear()


class TestCircuitBreakerStateMachine:
    def test_starts_closed(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        assert cb.allow_request() is True
        desc = cb.describe()
        assert desc["state"] == "closed"

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True  # still closed (2 < 3)

        cb.record_failure()
        assert cb.allow_request() is False  # now open
        assert cb.describe()["state"] == "open"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets count
        cb.record_failure()
        cb.record_failure()
        # Still only 2 failures since last success
        assert cb.allow_request() is True

    def test_open_to_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(service="test", failure_threshold=1, recovery_timeout_s=0.01)
        cb.record_failure()  # → OPEN
        assert cb.allow_request() is False

        time.sleep(0.02)  # wait for recovery timeout
        assert cb.allow_request() is True  # → HALF_OPEN (probe)
        assert cb.describe()["state"] == "half_open"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(
            service="test",
            failure_threshold=1,
            recovery_timeout_s=0.01,
            success_threshold=1,
        )
        cb.record_failure()  # → OPEN
        time.sleep(0.02)
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()  # → CLOSED
        assert cb.describe()["state"] == "closed"
        assert cb.allow_request() is True

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(service="test", failure_threshold=1, recovery_timeout_s=0.01)
        cb.record_failure()  # → OPEN
        time.sleep(0.02)
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()  # → OPEN again
        assert cb.describe()["state"] == "open"
        assert cb.allow_request() is False

    def test_success_threshold_multiple(self):
        """Require 2 successes to close from HALF_OPEN."""
        cb = CircuitBreaker(
            service="test",
            failure_threshold=1,
            recovery_timeout_s=0.01,
            success_threshold=2,
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()
        assert cb.describe()["state"] == "half_open"  # still half-open (1 < 2)
        cb.record_success()
        assert cb.describe()["state"] == "closed"


class TestDescribe:
    def test_describe_fields(self):
        cb = CircuitBreaker(service="svc_a")
        desc = cb.describe()
        assert desc["service"] == "svc_a"
        assert desc["state"] == "closed"
        assert desc["failure_count"] == 0
        assert desc["last_failure_age_s"] is None

    def test_describe_after_failure(self):
        cb = CircuitBreaker(service="svc_b")
        cb.record_failure()
        desc = cb.describe()
        assert desc["failure_count"] == 1
        assert isinstance(desc["last_failure_age_s"], float)


class TestRegistry:
    def test_get_breaker_creates_once(self):
        cb1 = get_breaker("alpha")
        cb2 = get_breaker("alpha")
        assert cb1 is cb2

    def test_get_breaker_different_services(self):
        cb1 = get_breaker("alpha")
        cb2 = get_breaker("beta")
        assert cb1 is not cb2

    def test_all_breakers_snapshot(self):
        get_breaker("a")
        get_breaker("b")
        snap = all_breakers()
        assert "a" in snap
        assert "b" in snap
        assert snap["a"]["state"] == "closed"
