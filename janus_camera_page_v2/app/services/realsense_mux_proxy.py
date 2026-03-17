"""Async HTTP proxy to the local RealSense MUX service.

The RealSense MUX runs on localhost and provides depth/IR/color frame
endpoints.  This module provides a pooled async httpx client so that
depth route handlers don't block the event loop with synchronous
requests.get() calls.

Uses ``AsyncProxyClient`` base class for lifecycle and error handling.
"""
from __future__ import annotations

import httpx

from app.core.settings import get_settings
from app.services.proxy_base import AsyncProxyClient

_proxy = AsyncProxyClient(
    "realsense_mux",
    connect_timeout=2.0,
    read_timeout=10.0,
    write_timeout=5.0,
    pool_timeout=30.0,
    max_keepalive=5,
    max_connections=20,
    extra_headers={"Connection": "keep-alive"},
)


async def start_client() -> None:
    await _proxy.start()


async def stop_client() -> None:
    await _proxy.stop()


async def get(path: str, params: dict | None = None) -> httpx.Response:
    """GET *path* from the realsense_mux_url, returning the raw httpx.Response.

    The caller is responsible for checking resp.status_code and raising
    HTTPException when the upstream returns a non-2xx status.
    """
    settings = get_settings()
    url = f"{settings.realsense_mux_url.rstrip('/')}{path}"
    return await _proxy.get(url, params=params)
