import asyncio
import logging

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.config import (
    CAMERA_SERVICES,
    MAX_REQUEST_BODY_BYTES,
    PROXY_BODY_TIMEOUT_S,
    PROXY_CONNECT_TIMEOUT_S,
    SERVICE_MAP,
)
from app.core.circuit_breaker import get_breaker
from app.core.http_client import (
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
from app.core.service_metrics import record_proxy_result

router = APIRouter(prefix="/api/v1", tags=["HTTP Proxy"])
METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]

logger = logging.getLogger(__name__)


def _normalize_upstream_path(service: str, upstream_path: str) -> str:
    if not upstream_path:
        return upstream_path
    trimmed = upstream_path.lstrip("/")
    duplicate = f"api/v1/{service}"
    if trimmed.startswith(duplicate):
        trimmed = trimmed[len(duplicate):]
    return trimmed.lstrip("/")


async def _read_body(request: Request) -> bytes:
    """Read request body with size guard to prevent OOM on huge uploads."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _guarded_body(
    resp: httpx.Response,
    sem: asyncio.Semaphore,
    body_timeout: float,
):
    """
    Async generator that streams the upstream response body.

    Owns the semaphore and response lifecycle:
    - Body streaming is bounded by body_timeout
    - resp.aclose() is guaranteed in finally
    - sem.release() is guaranteed in finally
    - Client disconnects (CancelledError) are handled gracefully
    """
    try:
        async with asyncio.timeout(body_timeout):
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
    except (asyncio.CancelledError, TimeoutError, GeneratorExit):
        # Client disconnected or body timeout — not an error, just cleanup.
        pass
    finally:
        try:
            await resp.aclose()
        except Exception:
            pass
        sem.release()


async def _proxy_to_service(request: Request, service: str, upstream_path: str):
    cfg = SERVICE_MAP.get(service)
    if not cfg:
        raise HTTPException(status_code=404, detail="Unknown service")

    # ── Circuit breaker check ───────────────────────────────────
    cb = get_breaker(service)
    request_id = request.scope.get("state", {}).get("request_id", "-")

    if not cb.allow_request():
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

    # ── Acquire semaphore + upstream request ────────────────────
    # Semaphore ownership transfers to _guarded_body on success;
    # released here explicitly on any connect/timeout error.
    sem = get_service_semaphore(request.app, service)
    sem_acquired = False

    try:
        async with asyncio.timeout(PROXY_CONNECT_TIMEOUT_S):
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
        if sem_acquired:
            sem.release()
        cb.record_failure()
        record_proxy_result(service, 502, reason="connect_failed")
        logger.error(
            "%s %s -> %s CONNECT_FAILED: %s",
            request.method, request.url.path, url, exc,
            extra={"request_id": request_id, "method": request.method, "url": url, "status": 502},
        )
        return JSONResponse(
            status_code=502,
            content={"detail": f"Upstream service unavailable: {service}", "url": url},
        )
    except (httpx.ReadTimeout, httpx.WriteTimeout, TimeoutError) as exc:
        if sem_acquired:
            sem.release()
        cb.record_failure()
        record_proxy_result(service, 504, reason="timeout")
        logger.error(
            "%s %s -> %s TIMEOUT: %s",
            request.method, request.url.path, url, exc,
            extra={"request_id": request_id, "method": request.method, "url": url, "status": 504},
        )
        return JSONResponse(
            status_code=504,
            content={"detail": f"Upstream service timeout: {service}", "url": url},
        )
    except httpx.HTTPError as exc:
        if sem_acquired:
            sem.release()
        cb.record_failure()
        record_proxy_result(service, 502, reason="upstream_http_error")
        logger.error(
            "%s %s -> %s HTTP_ERROR: %s",
            request.method, request.url.path, url, exc,
            extra={"request_id": request_id, "method": request.method, "url": url, "status": 502},
        )
        return JSONResponse(
            status_code=502,
            content={"detail": f"Upstream error: {service}", "url": url},
        )

    cb.record_success()
    record_proxy_result(service, resp.status_code)

    # Access log
    logger.info(
        "%s %s -> %s %d",
        request.method,
        request.url.path,
        url,
        resp.status_code,
        extra={"request_id": request_id, "method": request.method, "url": url, "status": resp.status_code},
    )

    # Semaphore ownership transfers to _guarded_body — released
    # in its finally block after body is fully streamed or on error.
    return StreamingResponse(
        content=_guarded_body(resp, sem, PROXY_BODY_TIMEOUT_S),
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
