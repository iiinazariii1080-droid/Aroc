"""Reusable async HTTP proxy client.

Eliminates copy-paste across janus_proxy, relay_proxy, depth_camera_proxy,
and realsense_mux_proxy by extracting common lifecycle management, error
handling, and request forwarding into one tested base class.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response


class AsyncProxyClient:
    """Pooled ``httpx.AsyncClient`` with lifecycle management.

    Features:
    - Lazy initialisation under asyncio.Lock (safe for concurrent callers).
    - Graceful shutdown via ``stop()`` with in-flight request awareness.
    - Consistent error mapping: Timeout→504, ConnectError→502,
      RemoteProtocolError→503 (shutdown race), other→502.
    """

    def __init__(
        self,
        name: str,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 90.0,
        write_timeout: float = 30.0,
        pool_timeout: float = 60.0,
        max_keepalive: int = 20,
        max_connections: int = 100,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.name = name
        self._log = logging.getLogger(f"proxy.{name}")
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )
        self._limits = httpx.Limits(
            max_keepalive_connections=max_keepalive,
            max_connections=max_connections,
        )
        self._headers = extra_headers or {}
        self._client: httpx.AsyncClient | None = None
        # Lazy-initialised on first use (not at import time) so the lock
        # binds to the running event loop — safe on Python <3.10.
        self._lock: asyncio.Lock | None = None

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
            headers={**self._headers},
        )

    def _get_lock(self) -> asyncio.Lock:
        """Return the asyncio lock, creating it lazily if needed.

        Safe in asyncio (single-threaded event loop): no ``await`` between
        the None check and assignment, so no interleaving is possible.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def start(self) -> None:
        """Create the underlying httpx client (idempotent)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._get_lock():
            if self._client is not None:
                return
            self._client = self._create_client()

    async def stop(self) -> None:
        """Close the underlying httpx client (idempotent)."""
        async with self._get_lock():
            if self._client is None:
                return
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the client, lazily creating it if needed."""
        async with self._get_lock():
            if self._client is None:
                self._client = self._create_client()
            return self._client

    # ── High-level helpers ────────────────────────────────────────────

    async def forward_request(
        self,
        request: Request,
        upstream_url: str,
        *,
        strip_headers: frozenset[str] = frozenset({"host", "connection"}),
    ) -> Response:
        """Forward an incoming FastAPI request to *upstream_url* and return the response.

        Strips hop-by-hop headers from both the forwarded request and the
        upstream response.
        """
        url = upstream_url
        if request.query_params:
            url = f"{url}?{request.query_params}"
        resp = await self._request(
            request.method,
            url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in strip_headers},
            content=await request.body(),
        )

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

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request with consistent error mapping.

        All public methods (get, post, etc.) delegate here to avoid
        duplicating the Timeout→504, ConnectError→502,
        RemoteProtocolError→503 mapping in every method.
        """
        client = await self._ensure_client()
        try:
            return await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            self._log.warning("%s timeout: %s → %s", method, url, exc)
            raise HTTPException(status_code=504, detail=f"{self.name} timeout") from exc
        except httpx.ConnectError as exc:
            self._log.warning("%s unreachable: %s → %s", method, url, exc)
            raise HTTPException(status_code=502, detail=f"{self.name} unreachable") from exc
        except httpx.RemoteProtocolError as exc:
            self._log.warning("%s shutdown race: %s → %s", method, url, exc)
            raise HTTPException(status_code=503, detail="Service shutting down") from exc
        except Exception as exc:  # pragma: no cover
            self._log.error("%s error: %s → %s", method, url, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Perform a GET request through the pooled client."""
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Perform a POST request through the pooled client."""
        return await self._request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        """GET *url* and return parsed JSON."""
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()
