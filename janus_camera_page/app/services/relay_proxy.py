"""Lightweight async HTTP proxy to the textroom relay for /time and /pong endpoints."""
from __future__ import annotations

from typing import Any, Dict

import httpx

from app.core.settings import get_settings
from app.services.proxy_base import AsyncHttpProxy

_proxy = AsyncHttpProxy(
    name="Relay",
    timeout=httpx.Timeout(connect=0.5, read=1.0, write=0.5, pool=1.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

start_client = _proxy.start
stop_client = _proxy.stop


async def relay_get(path: str) -> Dict[str, Any]:
    """GET a path on the relay and return parsed JSON."""
    settings = get_settings()
    url = f"{settings.relay_url}/{path.lstrip('/')}"
    return await _proxy.get_json(url)
