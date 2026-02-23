import asyncio
from typing import Any, Dict

import httpx
from fastapi import Request

from .config import HOP_BY_HOP_HEADERS, RETRY_ATTEMPTS, RETRY_BACKOFF_S


def join_url(base: str, *parts: str) -> str:
    url = base.rstrip("/")
    for p in parts:
        if p:
            url += "/" + p.lstrip("/")
    return url


def filter_response_headers(headers: httpx.Headers) -> Dict[str, str]:
    excluded = {"content-length", *HOP_BY_HOP_HEADERS}
    return {k: v for k, v in headers.items() if k.lower() not in excluded}


def forward_request_headers(request: Request) -> Dict[str, str]:
    excluded = {"host", "content-length", "accept-encoding"}
    return {k: v for k, v in request.headers.items() if k.lower() not in excluded}


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def stream_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    params: Any,
    content: bytes,
    timeout: httpx.Timeout | None = None,
    retry_attempts: int = RETRY_ATTEMPTS,
):
    can_retry = method.upper() in _SAFE_METHODS
    max_attempts = retry_attempts if can_retry else 0
    attempt = 0
    while True:
        try:
            req = client.build_request(
                method,
                url,
                headers=headers,
                params=params,
                content=content,
                timeout=timeout,
            )
            resp = await client.send(req, stream=True, follow_redirects=True)
            if resp.status_code in {502, 503, 504} and attempt < max_attempts:
                await resp.aclose()
                await asyncio.sleep(RETRY_BACKOFF_S * (2**attempt))
                attempt += 1
                continue
            return resp
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
        ) as exc:
            if attempt >= max_attempts:
                raise exc
            await asyncio.sleep(RETRY_BACKOFF_S * (2**attempt))
            attempt += 1
