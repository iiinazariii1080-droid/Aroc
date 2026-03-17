"""Lightweight async HTTP proxy to the textroom relay for /time and /pong endpoints.

Uses ``AsyncProxyClient`` base class for lifecycle and error handling.
"""
from __future__ import annotations

from typing import Any, Dict

from app.core.settings import get_settings
from app.services.proxy_base import AsyncProxyClient

_proxy = AsyncProxyClient(
    "relay",
    connect_timeout=0.5,
    read_timeout=1.0,
    write_timeout=0.5,
    pool_timeout=1.0,
    max_keepalive=5,
    max_connections=10,
)


async def start_client() -> None:
    await _proxy.start()


async def stop_client() -> None:
    await _proxy.stop()


async def relay_get(path: str) -> Dict[str, Any]:
    """GET a path on the relay and return parsed JSON."""
    settings = get_settings()
    url = f"{settings.relay_url}/{path.lstrip('/')}"
    return await _proxy.get_json(url)
