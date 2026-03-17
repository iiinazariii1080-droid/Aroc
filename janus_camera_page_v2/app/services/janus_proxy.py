"""Async HTTP proxy to the local Janus Gateway REST API.

Uses ``AsyncProxyClient`` base class for lifecycle and error handling.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from app.core.settings import get_settings
from app.services.proxy_base import AsyncProxyClient

_proxy = AsyncProxyClient(
    "janus",
    connect_timeout=5.0,
    read_timeout=90.0,
    write_timeout=30.0,
    pool_timeout=60.0,
    max_keepalive=20,
    max_connections=100,
    extra_headers={"Connection": "keep-alive"},
)


async def start_client() -> None:
    await _proxy.start()


async def stop_client() -> None:
    await _proxy.stop()


async def forward_request(request: Request) -> Response:
    settings = get_settings()
    url = f"{settings.janus_http_base.rstrip('/')}/janus"
    return await _proxy.forward_request(request, url)
