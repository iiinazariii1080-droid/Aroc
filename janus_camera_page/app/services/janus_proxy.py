from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response

from app.core.settings import get_settings

_janus_client: httpx.AsyncClient | None = None
_janus_client_lock = asyncio.Lock()


async def start_client() -> None:
    global _janus_client
    async with _janus_client_lock:
        if _janus_client is not None:
            return
        _janus_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=90.0, write=30.0, pool=60.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            headers={"Connection": "keep-alive"},
        )


async def stop_client() -> None:
    global _janus_client
    async with _janus_client_lock:
        if _janus_client is None:
            return
        await _janus_client.aclose()
        _janus_client = None


async def forward_request(request: Request) -> Response:
    if _janus_client is None:
        await start_client()
    client = _janus_client
    if client is None:
        raise HTTPException(status_code=503, detail="Janus proxy client not ready")

    settings = get_settings()
    url = f"{settings.janus_http_base.rstrip('/')}/janus"
    if request.query_params:
        url = f"{url}?{request.query_params}"
    try:
        resp = await client.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
        )
    except httpx.TimeoutException as exc:
        logging.warning("Janus long-poll timeout: %s", exc)
        raise HTTPException(status_code=504, detail="Janus long-poll timeout") from exc
    except Exception as exc:  # pragma: no cover - defensive
        logging.error("Janus proxy error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )

