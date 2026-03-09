"""Lightweight async HTTP proxy to the textroom relay for /time and /pong endpoints."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

import httpx

from app.core.settings import get_settings

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def start_client() -> None:
    global _client
    async with _client_lock:
        if _client is not None:
            return
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=0.5, read=1.0, write=0.5, pool=1.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )


async def stop_client() -> None:
    global _client
    async with _client_lock:
        if _client is None:
            return
        await _client.aclose()
        _client = None


async def relay_get(path: str) -> Dict[str, Any]:
    """GET a path on the relay and return parsed JSON."""
    if _client is None:
        await start_client()
    client = _client
    if client is None:
        raise RuntimeError("Relay proxy client not ready")

    settings = get_settings()
    url = f"{settings.relay_url}/{path.lstrip('/')}"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()
