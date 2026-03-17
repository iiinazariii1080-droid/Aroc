"""Async HTTP + WS proxy to the depth camera at 192.168.1.55:8900.

Only active when *this* instance runs as ``color_camera`` (the gateway host).
The depth camera is connected via an isolated WiFi router and is unreachable
from the internet — all browser traffic for the depth stream is relayed through
here.

Uses ``AsyncProxyClient`` base class for lifecycle and error handling.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from app.core.settings import get_settings
from app.services.proxy_base import AsyncProxyClient

_proxy = AsyncProxyClient(
    "depth_camera",
    connect_timeout=5.0,
    read_timeout=90.0,
    write_timeout=30.0,
    pool_timeout=60.0,
    max_keepalive=10,
    max_connections=50,
    extra_headers={"Connection": "keep-alive"},
)


async def start_client() -> None:
    await _proxy.start()


async def stop_client() -> None:
    await _proxy.stop()


async def forward_request(request: Request, upstream_path: str) -> Response:
    """Forward an arbitrary HTTP request to the depth camera.

    ``upstream_path`` is the path on the depth camera server
    (e.g. ``/janus``, ``/client-config``, ``/snapshot.jpg``).
    """
    settings = get_settings()
    base = settings.depth_cam_url.rstrip("/")
    url = f"{base}{upstream_path}"
    return await _proxy.forward_request(request, url)
