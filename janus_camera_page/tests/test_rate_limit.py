"""Tests for the per-IP token bucket rate limiter middleware.

Validates:
- Token bucket replenishment over time
- Burst capacity exhaustion → 429
- Admin rate limiter stricter than general
- Stale bucket cleanup
- Per-IP isolation

Markers: unit
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.middleware.rate_limit import (
    _Bucket,
    _buckets,
    _admin_buckets,
    require_rate_limit,
    require_admin_rate_limit,
)


class TestBucket:
    """Unit tests for the _Bucket token bucket implementation."""

    def test_initial_tokens_equal_burst(self):
        b = _Bucket(rate=10, burst=20)
        assert b.tokens == 20

    def test_consume_decrements_token(self):
        b = _Bucket(rate=10, burst=5)
        assert b.consume() is True
        assert b.tokens == 4.0

    def test_burst_exhaustion(self):
        b = _Bucket(rate=10, burst=3)
        assert b.consume() is True   # 2 left
        assert b.consume() is True   # 1 left
        assert b.consume() is True   # 0 left
        assert b.consume() is False  # exhausted

    def test_replenishment_over_time(self):
        b = _Bucket(rate=10, burst=5)
        # Exhaust all tokens
        for _ in range(5):
            b.consume()
        assert b.consume() is False

        # Simulate 0.5s passing → should replenish 5 tokens
        b.last -= 0.5
        assert b.consume() is True

    def test_tokens_capped_at_burst(self):
        b = _Bucket(rate=100, burst=5)
        # Simulate long idle period
        b.last -= 100.0
        b.consume()
        # Tokens should not exceed burst
        assert b.tokens <= 5.0


class TestRateLimitDependency:
    """Integration tests for require_rate_limit FastAPI dependency."""

    @pytest.fixture(autouse=True)
    def _clear_buckets(self):
        _buckets.clear()
        yield
        _buckets.clear()

    @pytest.mark.asyncio
    async def test_allows_normal_traffic(self):
        request = MagicMock()
        request.client.host = "10.0.0.1"
        # Should not raise for first request
        await require_rate_limit(request)

    @pytest.mark.asyncio
    async def test_rejects_after_burst_exhausted(self):
        request = MagicMock()
        request.client.host = "10.0.0.99"

        # Exhaust burst (default 60)
        for _ in range(60):
            await require_rate_limit(request)

        # Next should be rejected
        with pytest.raises(HTTPException) as exc_info:
            await require_rate_limit(request)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_per_ip_isolation(self):
        req_a = MagicMock()
        req_a.client.host = "10.0.0.1"
        req_b = MagicMock()
        req_b.client.host = "10.0.0.2"

        # Exhaust IP A
        for _ in range(60):
            await require_rate_limit(req_a)

        # IP B should still be allowed
        await require_rate_limit(req_b)  # no raise

    @pytest.mark.asyncio
    async def test_unknown_client_ip(self):
        request = MagicMock()
        request.client = None
        # Should use "unknown" key, not crash
        await require_rate_limit(request)


class TestAdminRateLimitDependency:
    """Integration tests for require_admin_rate_limit (stricter)."""

    @pytest.fixture(autouse=True)
    def _clear_buckets(self):
        _admin_buckets.clear()
        yield
        _admin_buckets.clear()

    @pytest.mark.asyncio
    async def test_admin_allows_first_request(self):
        request = MagicMock()
        request.client.host = "10.0.0.1"
        await require_admin_rate_limit(request)

    @pytest.mark.asyncio
    async def test_admin_rejects_after_burst(self):
        request = MagicMock()
        request.client.host = "10.0.0.50"

        # Admin burst is 5 (default)
        for _ in range(5):
            await require_admin_rate_limit(request)

        with pytest.raises(HTTPException) as exc_info:
            await require_admin_rate_limit(request)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_admin_stricter_than_general(self):
        """Admin limit (5/min) exhausts much sooner than general (60 burst)."""
        request = MagicMock()
        request.client.host = "10.0.0.77"

        admin_count = 0
        for _ in range(100):
            try:
                await require_admin_rate_limit(request)
                admin_count += 1
            except HTTPException:
                break

        # Should exhaust after 5 requests
        assert admin_count == 5


class TestStaleCleanup:
    """Stale bucket entries are cleaned up after TTL."""

    @pytest.fixture(autouse=True)
    def _clear_buckets(self):
        _buckets.clear()
        yield
        _buckets.clear()

    @pytest.mark.asyncio
    async def test_stale_entries_removed(self):
        request = MagicMock()
        request.client.host = "10.0.0.1"
        await require_rate_limit(request)
        assert "10.0.0.1" in _buckets

        # Make the bucket stale
        _buckets["10.0.0.1"].last -= 120.0

        # Trigger cleanup by making another request from different IP
        import app.middleware.rate_limit as rl
        original = rl._last_cleanup
        rl._last_cleanup -= 120.0  # force cleanup on next call

        request2 = MagicMock()
        request2.client.host = "10.0.0.2"
        await require_rate_limit(request2)

        assert "10.0.0.1" not in _buckets
        rl._last_cleanup = original
