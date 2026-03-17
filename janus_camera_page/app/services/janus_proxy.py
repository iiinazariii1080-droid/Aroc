from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import Response

from app.core.settings import get_settings
from app.services.proxy_base import AsyncHttpProxy

_proxy = AsyncHttpProxy(
    name="Janus",
    timeout=httpx.Timeout(connect=5.0, read=90.0, write=30.0, pool=60.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    extra_headers={"Connection": "keep-alive"},
)

start_client = _proxy.start
stop_client = _proxy.stop


async def forward_request(request: Request) -> Response:
    settings = get_settings()
    url = f"{settings.janus_http_base.rstrip('/')}/janus"
    return await _proxy.forward(request, url)
