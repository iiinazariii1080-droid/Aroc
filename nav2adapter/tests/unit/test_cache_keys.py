"""Unit tests for cache key stability and collision prevention.

These tests ensure that cache keys for bound methods include a stable client identity
(e.g., base_url + robot_number) so different client instances cannot share cached
responses erroneously.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.cache import cached, cache


class DummyClient:
    def __init__(self, base_url: str, robot_number: int):
        self.base_url = base_url
        self.robot_number = robot_number

    @cached(ttl=5, key_prefix="dummy")
    async def foo(self) -> int:
        return 1


@pytest.mark.asyncio
async def test_cache_keys_differ_for_different_client_identity():
    await cache.clear()

    a = DummyClient(base_url="http://10.0.0.1", robot_number=1)
    b = DummyClient(base_url="http://10.0.0.2", robot_number=1)

    keys = []

    async def _capture_set(key, value, ttl=None):
        keys.append(key)
        # call original set to keep behavior realistic
        return await original_set(key, value, ttl)

    original_set = cache.set

    with patch.object(cache, "get", new=AsyncMock(return_value=None)):
        with patch.object(cache, "set", new=_capture_set):
            await a.foo()
            await b.foo()

    assert len(keys) == 2
    assert keys[0] != keys[1], "Different client identities must not share the same cache key"
