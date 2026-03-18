import asyncio
import logging
import time
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.circuit_breaker import get_breaker
from app.core.config import CAMERA_SERVICES, SERVICE_MAP, settings
from app.core.lifecycle import (
    get_http_client,
    get_cam_http_client,
    get_service_semaphore,
)
from app.core.http_proxy_utils import (
    join_url,
    filter_response_headers,
    forward_request_headers,
    stream_request,
)
from app.core.service_metrics import record_proxy_duration, record_proxy_result

router = APIRouter(prefix="/api/v1", tags=["HTTP Proxy"])
METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]

logger = logging.getLogger(__name__)


def _normalize_upstream_path(service: str, upstream_path: str) -> str:
    if not upstream_path:
        return upstream_path
    trimmed = upstream_path.lstrip("/")
    duplicate = f"api/v1/{service}"
    if trimmed == duplicate or trimmed.startswith(duplicate + "/"):
        trimmed = trimmed[len(duplicate):]
    trimmed = trimmed.lstrip("/")
    # Reject path traversal attempts — decode first to catch %2e%2e etc.
    decoded = unquote(trimmed).replace("\\", "/")
    segments = decoded.split("/")
    if any(seg == ".." for seg in segments):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    return trimmed


async def _read_body(request: Request) -> bytes:
    """Read request body with size and time guards."""
    chunks: list[bytes] = []
    total = 0
    async with asyncio.timeout(settings.body_read_timeout):  # slowloris protection
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.max_request_body_bytes:
                raise HTTPException(status_code=413, detail="Request body too large")
            chunks.append(chunk)
    return b"".join(chunks)


async def _guarded_body_gen(
    resp: httpx.Response,
    sem: asyncio.Semaphore,
    released: list[bool],
    body_timeout: float,
):
    """
    Async generator that streams the upstream response body.

    Owns the semaphore and response lifecycle:
    - Body streaming is bounded by body_timeout
    - resp.aclose() is guaranteed in finally
    - sem.release() is guaranteed exactly once via *released* flag
    - CancelledError is re-raised after cleanup to preserve asyncio
      cancellation semantics
    """
    cancelled = False
    try:
        async with asyncio.timeout(body_timeout):
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
    except asyncio.CancelledError:
        cancelled = True
    except (TimeoutError, GeneratorExit):
        pass
    finally:
        try:
            await resp.aclose()
        except Exception:
            pass
        if not released[0]:
            released[0] = True
            sem.release()
        if cancelled:
            raise asyncio.CancelledError()


class _GuardedBody:
    """Wrapper around the streaming async generator that guarantees semaphore
    release even if the generator is never iterated (e.g. client disconnect
    before Starlette starts streaming, or middleware discarding the response).

    Python's ``aclose()`` on an un-started async generator is a no-op — the
    ``finally`` block never runs.  This wrapper intercepts ``aclose()`` and
    performs cleanup explicitly when the generator was never entered.

    A shared ``_released`` flag (mutable list) prevents double-release in
    edge cases where both the generator's finally and aclose() run.
    """

    __slots__ = ("_gen", "_sem", "_resp", "_started", "_released")

    def __init__(
        self,
        gen,
        sem: asyncio.Semaphore,
        resp: httpx.Response,
        released: list[bool],
    ) -> None:
        self._gen = gen
        self._sem = sem
        self._resp = resp
        self._started = False
        self._released = released

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._started = True
        return await self._gen.__anext__()

    async def aclose(self) -> None:
        if self._started:
            await self._gen.aclose()
        else:
            # Generator was never iterated — clean up manually
            try:
                await self._resp.aclose()
            except Exception:
                pass
        if not self._released[0]:
            self._released[0] = True
            self._sem.release()

    async def athrow(self, typ, val=None, tb=None):
        return await self._gen.athrow(typ, val, tb)


async def _handle_upstream_error(
    *,
    exc: Exception,
    service: str,
    status_code: int,
    reason: str,
    detail: str,
    request: Request,
    url: str,
    t0: float,
    sem: asyncio.Semaphore,
    sem_acquired: bool,
    cb,
    request_id: str,
) -> JSONResponse:
    """Shared error handler for upstream connect/timeout/HTTP failures."""
    if sem_acquired:
        sem.release()
    await cb.record_failure()
    duration_ms = round((time.monotonic() - t0) * 1000, 2)
    record_proxy_result(service, status_code, reason=reason)
    record_proxy_duration(service, duration_ms / 1000.0)
    logger.error(
        "%s %s -> %s %s: %s",
        request.method, request.url.path, url, reason.upper(), exc,
        extra={"request_id": request_id, "method": request.method,
               "url": url, "status": status_code, "duration_ms": duration_ms},
    )
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )


async def _proxy_to_service(request: Request, service: str, upstream_path: str):
    cfg = SERVICE_MAP.get(service)
    if not cfg:
        raise HTTPException(status_code=404, detail="Unknown service")

    # ── Circuit breaker check ───────────────────────────────────
    cb = await get_breaker(service)
    request_id = request.scope.get("state", {}).get("request_id", "-")

    if not await cb.allow_request():
        record_proxy_result(service, 502, reason="circuit_open")
        logger.debug(
            "%s %s CIRCUIT_OPEN for %s",
            request.method, request.url.path, service,
            extra={"request_id": request_id, "method": request.method, "status": 502},
        )
        return JSONResponse(
            status_code=502,
            content={"detail": f"Service temporarily unavailable (circuit open): {service}"},
        )

    base = cfg["url"]
    upstream_prefix = cfg.get("prefix", "")

    # ── Pick the right HTTP client ──────────────────────────────
    if service in CAMERA_SERVICES:
        client = get_cam_http_client(request.app)
    else:
        client = get_http_client(request.app)

    want_trailing = request.url.path.endswith("/")
    normalized_path = _normalize_upstream_path(service, upstream_path)
    url = join_url(base, upstream_prefix, normalized_path)
    if want_trailing and not url.endswith("/"):
        url += "/"

    # Read request body with size guard
    body = await _read_body(request)

    t0 = time.monotonic()

    # ── Acquire semaphore + upstream request ────────────────────
    # Semaphore ownership transfers to _guarded_body on success;
    # released here explicitly on any connect/timeout error.
    sem = get_service_semaphore(request.app, service)
    sem_acquired = False

    try:
        async with asyncio.timeout(settings.proxy_connect_timeout):
            await sem.acquire()
            sem_acquired = True
            resp = await stream_request(
                client,
                request.method,
                url,
                headers=forward_request_headers(request),
                params=request.query_params,
                content=body,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        return await _handle_upstream_error(
            exc=exc, service=service, status_code=502, reason="connect_failed",
            detail=f"Upstream service unavailable: {service}",
            request=request, url=url, t0=t0, sem=sem,
            sem_acquired=sem_acquired, cb=cb, request_id=request_id,
        )
    except (httpx.ReadTimeout, httpx.WriteTimeout, TimeoutError) as exc:
        return await _handle_upstream_error(
            exc=exc, service=service, status_code=504, reason="timeout",
            detail=f"Upstream service timeout: {service}",
            request=request, url=url, t0=t0, sem=sem,
            sem_acquired=sem_acquired, cb=cb, request_id=request_id,
        )
    except httpx.HTTPError as exc:
        return await _handle_upstream_error(
            exc=exc, service=service, status_code=502, reason="upstream_http_error",
            detail=f"Upstream error: {service}",
            request=request, url=url, t0=t0, sem=sem,
            sem_acquired=sem_acquired, cb=cb, request_id=request_id,
        )

    if resp.status_code >= 500:
        await cb.record_failure()
    else:
        await cb.record_success()
    record_proxy_result(service, resp.status_code)

    duration_ms = round((time.monotonic() - t0) * 1000, 2)
    record_proxy_duration(service, duration_ms / 1000.0)
    # Access log
    logger.info(
        "%s %s -> %s %d (%.1fms)",
        request.method,
        request.url.path,
        url,
        resp.status_code,
        duration_ms,
        extra={"request_id": request_id, "method": request.method, "url": url, "status": resp.status_code, "duration_ms": duration_ms},
    )

    # Semaphore ownership transfers to _GuardedBody — released
    # in the generator's finally block after body is fully streamed,
    # or by the wrapper's aclose() if the generator is never iterated.
    # The shared released flag prevents double-release in edge cases.
    released = [False]
    raw_gen = _guarded_body_gen(resp, sem, released, settings.proxy_body_timeout)
    body = _GuardedBody(raw_gen, sem, resp, released)
    return StreamingResponse(
        content=body,
        status_code=resp.status_code,
        headers=filter_response_headers(resp.headers),
    )


@router.api_route("/{service}/{path:path}", methods=METHODS, include_in_schema=False)
async def proxy(service: str, path: str, request: Request):
    return await _proxy_to_service(request, service, path)


@router.api_route("/{service}", methods=METHODS, include_in_schema=False)
@router.api_route("/{service}/", methods=METHODS, include_in_schema=False)
async def proxy_root(service: str, request: Request):
    return await _proxy_to_service(request, service, "")
