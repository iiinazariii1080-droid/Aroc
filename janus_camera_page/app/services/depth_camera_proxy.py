"""Async HTTP + WS proxy to the depth camera at 192.168.1.55:8900.

Only active when *this* instance runs as ``color_camera`` (the gateway host).
The depth camera is connected via an isolated WiFi router and is unreachable
from the internet — all browser traffic for the depth stream is relayed through
here.

Usage (called from ``events.py / routes/__init__.py``):
    await depth_camera_proxy.start_client()
    ...
    await depth_camera_proxy.stop_client()
"""
from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response

from app.core.settings import get_settings

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


async def start_client() -> None:
    global _client
    if _client is not None:
        return
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=90.0, write=30.0, pool=60.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
        headers={"Connection": "keep-alive"},
    )


async def stop_client() -> None:
    global _client
    if _client is None:
        return
    await _client.aclose()
    _client = None


async def forward_request(request: Request, upstream_path: str) -> Response:
    """Forward an arbitrary HTTP request to the depth camera.

    ``upstream_path`` is the path on the depth camera server
    (e.g. ``/janus``, ``/client-config``, ``/snapshot.jpg``).
    """
    global _client
    if _client is None:
        await start_client()
    assert _client is not None

    settings = get_settings()
    base = settings.depth_camera_url.rstrip("/")
    url = f"{base}{upstream_path}"
    if request.query_params:
        url = f"{url}?{request.query_params}"

    try:
        resp = await _client.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "connection")},
            content=await request.body(),
        )
    except httpx.TimeoutException as exc:
        log.warning("Depth camera proxy timeout: %s → %s", upstream_path, exc)
        raise HTTPException(status_code=504, detail="Depth camera proxy timeout") from exc
    except httpx.ConnectError as exc:
        log.warning("Depth camera unreachable: %s → %s", upstream_path, exc)
        raise HTTPException(status_code=502, detail="Depth camera unreachable") from exc
    except Exception as exc:  # pragma: no cover
        log.error("Depth camera proxy error: %s → %s", upstream_path, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Strip hop-by-hop headers that shouldn't be forwarded.
    fwd_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "connection", "keep-alive")
    }

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=fwd_headers,
        media_type=resp.headers.get("content-type"),
    )
