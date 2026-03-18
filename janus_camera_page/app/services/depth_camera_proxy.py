"""Async HTTP proxy to the depth camera at 192.168.1.55:8900.

Only active when this instance runs as ``color_camera`` (the gateway host).
The depth camera is on an isolated WiFi router — all browser traffic for the
depth stream is relayed through here.
"""
from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import Response

from app.core.settings import get_settings
from app.services.proxy_base import AsyncHttpProxy

_proxy = AsyncHttpProxy(
    name="Depth camera",
    timeout=httpx.Timeout(connect=5.0, read=90.0, write=30.0, pool=60.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
    extra_headers={"Connection": "keep-alive"},
    strip_headers=("host", "connection"),
)

start_client = _proxy.start
stop_client = _proxy.stop


async def forward_request(request: Request, upstream_path: str) -> Response:
    """Forward an HTTP request to the depth camera."""
    settings = get_settings()
    url = f"{settings.depth_cam_url.rstrip('/')}{upstream_path}"
    return await _proxy.forward(request, url)


async def get(path: str, **params: str) -> "httpx.Response":
    """Direct GET to depth camera (reuses the managed client pool)."""
    settings = get_settings()
    url = f"{settings.depth_cam_url.rstrip('/')}{path}"
    return await _proxy.get(url, **params)
