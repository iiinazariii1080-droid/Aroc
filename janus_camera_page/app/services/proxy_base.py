"""Reusable async HTTP proxy client with lifecycle management.

Eliminates copy-paste between janus_proxy, depth_camera_proxy, and relay_proxy.
Each proxy configures its own timeout/limits via constructor parameters.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response

log = logging.getLogger(__name__)


class AsyncHttpProxy:
    """Singleton-pattern async HTTP proxy with start/stop lifecycle."""

    def __init__(
        self,
        name: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        extra_headers: Optional[Dict[str, str]] = None,
        strip_headers: tuple[str, ...] = ("host",),
    ) -> None:
        self.name = name
        self._timeout = timeout
        self._limits = limits
        self._extra_headers = extra_headers or {}
        self._strip_headers = strip_headers
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._client is not None:
                return
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=self._limits,
                headers=self._extra_headers,
            )

    async def stop(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.start()
        client = self._client
        if client is None:
            raise HTTPException(status_code=503, detail=f"{self.name} proxy client not ready")
        return client

    async def forward(self, request: Request, url: str) -> Response:
        """Forward an HTTP request to the given URL."""
        client = await self._ensure_client()
        if request.query_params:
            url = f"{url}?{request.query_params}"
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in self._strip_headers
        }
        try:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=await request.body(),
            )
        except httpx.TimeoutException as exc:
            log.warning("%s proxy timeout: %s", self.name, exc)
            raise HTTPException(status_code=504, detail=f"{self.name} proxy timeout") from exc
        except httpx.ConnectError as exc:
            log.warning("%s unreachable: %s", self.name, exc)
            raise HTTPException(status_code=502, detail=f"{self.name} unreachable") from exc
        except Exception as exc:
            log.error("%s proxy error: %s", self.name, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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

    async def get(self, url: str, **params: str) -> httpx.Response:
        """Direct GET returning the raw httpx Response."""
        client = await self._ensure_client()
        return await client.get(url, params=params if params else None)

    async def get_json(self, url: str) -> Dict[str, Any]:
        """Simple GET → JSON for lightweight proxies (relay)."""
        client = await self._ensure_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
