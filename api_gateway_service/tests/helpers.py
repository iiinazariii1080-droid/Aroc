"""Shared test helpers — import from test modules, not from conftest."""

import asyncio
from unittest.mock import AsyncMock

import httpx


def make_mock_response(
    status_code: int = 200,
    body: bytes = b'{"ok":true}',
    headers: dict | None = None,
    *,
    delay: float = 0.0,
    chunk_size: int | None = None,
):
    """Create a mock httpx.Response suitable for streaming proxy tests.

    Args:
        delay: seconds to sleep between chunks (simulates slow upstream).
        chunk_size: if set, split body into chunks of this size.
    """
    resp = AsyncMock()
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {"content-type": "application/json"})

    if chunk_size and len(body) > chunk_size:
        chunks = [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)]
    else:
        chunks = [body]

    async def _aiter_bytes(chunk_size=65536):
        for c in chunks:
            if delay > 0:
                await asyncio.sleep(delay)
            yield c

    resp.aiter_bytes = _aiter_bytes
    resp.aclose = AsyncMock()
    return resp
