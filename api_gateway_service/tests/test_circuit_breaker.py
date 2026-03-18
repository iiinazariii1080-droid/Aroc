"""Tests for CircuitBreaker (app/core/circuit_breaker.py).

Covers CLOSED → OPEN → HALF_OPEN state transitions, recovery timing,
success/failure counting, registry, and describe().
"""
from __future__ import annotations

import asyncio
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
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        assert await cb.allow_request() is True
        desc = await cb.describe()
        assert desc["state"] == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.allow_request() is True  # still closed (2 < 3)

        await cb.record_failure()
        assert await cb.allow_request() is False  # now open
        assert (await cb.describe())["state"] == "open"

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(service="test", failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()  # resets count
        await cb.record_failure()
        await cb.record_failure()
        # Still only 2 failures since last success
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(service="test", failure_threshold=1, recovery_timeout_s=0.01)
        await cb.record_failure()  # → OPEN
        assert await cb.allow_request() is False

        await asyncio.sleep(0.02)  # wait for recovery timeout
        assert await cb.allow_request() is True  # → HALF_OPEN (probe)
        assert (await cb.describe())["state"] == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_success_closes(self):
        cb = CircuitBreaker(
            service="test",
            failure_threshold=1,
            recovery_timeout_s=0.01,
            success_threshold=1,
        )
        await cb.record_failure()  # → OPEN
        await asyncio.sleep(0.02)
        await cb.allow_request()  # → HALF_OPEN
        await cb.record_success()  # → CLOSED
        assert (await cb.describe())["state"] == "closed"
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(service="test", failure_threshold=1, recovery_timeout_s=0.01)
        await cb.record_failure()  # → OPEN
        await asyncio.sleep(0.02)
        await cb.allow_request()  # → HALF_OPEN
        await cb.record_failure()  # → OPEN again
        assert (await cb.describe())["state"] == "open"
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_success_threshold_multiple(self):
        """Require 2 successes to close from HALF_OPEN."""
        cb = CircuitBreaker(
            service="test",
            failure_threshold=1,
            recovery_timeout_s=0.01,
            success_threshold=2,
        )
        await cb.record_failure()
        await asyncio.sleep(0.02)
        await cb.allow_request()  # → HALF_OPEN
        await cb.record_success()
        assert (await cb.describe())["state"] == "half_open"  # still half-open (1 < 2)
        await cb.record_success()
        assert (await cb.describe())["state"] == "closed"


class TestDescribe:
    @pytest.mark.asyncio
    async def test_describe_fields(self):
        cb = CircuitBreaker(service="svc_a")
        desc = await cb.describe()
        assert desc["service"] == "svc_a"
        assert desc["state"] == "closed"
        assert desc["failure_count"] == 0
        assert desc["last_failure_age_s"] is None

    @pytest.mark.asyncio
    async def test_describe_after_failure(self):
        cb = CircuitBreaker(service="svc_b")
        await cb.record_failure()
        desc = await cb.describe()
        assert desc["failure_count"] == 1
        assert isinstance(desc["last_failure_age_s"], float)


class TestRegistry:
    @pytest.mark.asyncio
    async def test_get_breaker_creates_once(self):
        cb1 = await get_breaker("alpha")
        cb2 = await get_breaker("alpha")
        assert cb1 is cb2

    @pytest.mark.asyncio
    async def test_get_breaker_different_services(self):
        cb1 = await get_breaker("alpha")
        cb2 = await get_breaker("beta")
        assert cb1 is not cb2

    @pytest.mark.asyncio
    async def test_all_breakers_snapshot(self):
        await get_breaker("a")
        await get_breaker("b")
        snap = await all_breakers()
        assert "a" in snap
        assert "b" in snap
        assert snap["a"]["state"] == "closed"
