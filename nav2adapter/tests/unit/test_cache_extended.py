"""Tests for app/cache — SimpleCache TTL, cached() decorator, cache_invalidate()."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from app.cache import SimpleCache, cached, cache_invalidate, cache


# ── SimpleCache ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cache_get_set():
    c = SimpleCache(default_ttl=60)
    await c.set("k", "v")
    assert await c.get("k") == "v"


@pytest.mark.asyncio
async def test_cache_ttl_expiry():
    """Expired entry returns None and is evicted."""
    c = SimpleCache(default_ttl=60)
    await c.set("k", "v", ttl=1)
    # Patch time to simulate expiry
    real_entries = c._cache
    real_entries["k"]["expires_at"] = time.time() - 1
    assert await c.get("k") is None
    assert "k" not in c._cache


@pytest.mark.asyncio
async def test_cache_miss():
    c = SimpleCache()
    assert await c.get("nonexistent") is None


@pytest.mark.asyncio
async def test_cache_clear():
    c = SimpleCache()
    await c.set("a", 1)
    await c.set("b", 2)
    await c.clear()
    assert await c.get("a") is None


@pytest.mark.asyncio
async def test_cache_invalidate_key():
    c = SimpleCache()
    await c.set("x", 10)
    await c.invalidate("x")
    assert await c.get("x") is None


@pytest.mark.asyncio
async def test_cache_invalidate_missing_key():
    c = SimpleCache()
    await c.invalidate("no_such_key")  # no error


# ── cached() decorator ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_cached_decorator_miss_then_hit():
    """First call executes function; second returns cached value."""
    call_count = 0

    @cached(ttl=300, key_prefix="test")
    async def expensive(x: int):
        nonlocal call_count
        call_count += 1
        return x * 2

    await cache.clear()
    r1 = await expensive(5)
    r2 = await expensive(5)
    assert r1 == r2 == 10
    assert call_count == 1  # only called once


@pytest.mark.asyncio
async def test_cached_decorator_different_args():
    """Different args produce different cache keys."""
    @cached(ttl=300, key_prefix="diff")
    async def fn(a, b=0):
        return a + b

    await cache.clear()
    assert await fn(1, b=2) == 3
    assert await fn(1, b=3) == 4


@pytest.mark.asyncio
async def test_cached_bound_method_identity():
    """Bound methods use client identity, not object repr."""

    class Client:
        def __init__(self, base_url: str):
            self.base_url = base_url

        @cached(ttl=300, key_prefix="client")
        async def fetch(self, endpoint: str):
            return f"{self.base_url}/{endpoint}"

    await cache.clear()
    c = Client("http://robot:8080")
    r = await c.fetch("status")
    assert r == "http://robot:8080/status"
    # Second call should come from cache (identity includes base_url)
    r2 = await c.fetch("status")
    assert r2 == r


# ── cache_invalidate() decorator ────────────────────────────────
@pytest.mark.asyncio
async def test_cache_invalidate_decorator_evicts():
    """cache_invalidate removes matching keys after call."""
    await cache.clear()
    await cache.set("symovo_station:abc123", {"data": True})
    await cache.set("symovo_station_list:xyz", {"data": True})
    await cache.set("other_key", 42)

    @cache_invalidate("symovo_station")
    async def update():
        return "ok"

    result = await update()
    assert result == "ok"
    # Both keys contain "symovo_station" substring → evicted
    assert await cache.get("symovo_station:abc123") is None
    assert await cache.get("symovo_station_list:xyz") is None
    # Other key untouched
    assert await cache.get("other_key") == 42


@pytest.mark.asyncio
async def test_cache_invalidate_no_match():
    """When no keys match pattern, nothing is removed."""
    await cache.clear()
    await cache.set("foo", 1)

    @cache_invalidate("bar")
    async def action():
        return "done"

    await action()
    assert await cache.get("foo") == 1
