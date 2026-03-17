"""HTTP middleware: request-ID injection, legacy deprecation headers, metrics."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app import error_codes
from app.config import Settings, get_legacy_api_phase, get_settings
from app.http_errors import error_detail
from app.metrics import MetricsRegistry
from app.request_context import reset_request_id, set_request_id
from app.routes import LEGACY_ENDPOINTS

_SAFE_REQUEST_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]")
_MAX_REQUEST_ID_LEN = 64


def _sanitize_request_id(raw: str) -> str:
    """Strip non-alphanumeric characters and truncate to prevent log injection."""
    return _SAFE_REQUEST_ID_RE.sub("", raw)[:_MAX_REQUEST_ID_LEN]


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    metrics: MetricsRegistry,
) -> Response:
    """Inject ``X-Request-ID``, apply legacy deprecation headers, record latency."""
    raw_id = request.headers.get("X-Request-ID")
    request_id = _sanitize_request_id(raw_id) if raw_id else uuid.uuid4().hex
    if not request_id:
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    token = set_request_id(request_id)
    started = time.monotonic()
    logger = logging.getLogger("http.request")
    try:
        is_legacy_path = request.url.path in LEGACY_ENDPOINTS
        # Read Settings from app.state to avoid
        # calling get_settings() on every request.
        settings_obj = getattr(getattr(request.app, "state", None), "settings", None)
        s = settings_obj if isinstance(settings_obj, Settings) else get_settings()
        # Read the cached phase computed at startup — avoids date parsing per request.
        legacy_phase = getattr(
            getattr(request.app, "state", None),
            "legacy_api_phase",
            None,
        ) or get_legacy_api_phase()

        if is_legacy_path:
            metrics.observe_legacy_api_request(path=request.url.path, phase=legacy_phase)

        if is_legacy_path and legacy_phase == "removed":
            payload = error_detail(
                code=error_codes.LEGACY_API_REMOVED.code,
                message=error_codes.LEGACY_API_REMOVED.message,
                details={
                    "request_id": request_id,
                    "successor_path": s.legacy_api_successor_path,
                },
            )
            return JSONResponse(
                status_code=410,
                content=payload,
                headers={"X-Request-ID": request_id, "X-API-Phase": "removed"},
            )

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if s.legacy_api_deprecation and is_legacy_path:
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = s.legacy_api_sunset
            response.headers["Link"] = (
                f'<{s.legacy_api_docs_link}>; rel="alternate", '
                f'<{s.legacy_api_successor_path}>; rel="successor-version"'
            )
            response.headers["X-API-Phase"] = legacy_phase
        elapsed_ms = (time.monotonic() - started) * 1000.0
        # SSE streams are long-lived connections; recording their duration as request
        # latency would skew the histogram with multi-minute values.  Skip latency
        # recording for event-stream responses — they have their own observability
        # via the event bus ring buffer and SSE subscriber metrics.
        is_sse = getattr(response, "media_type", None) == "text/event-stream"
        if not is_sse:
            metrics.observe_http(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
            )
        logger.info(
            "%s %s -> %d in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
    finally:
        reset_request_id(token)
