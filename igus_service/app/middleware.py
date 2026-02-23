"""HTTP middleware: request-ID injection, legacy deprecation headers, metrics."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app import config as app_config
from app import error_codes
from app.http_errors import error_detail
from app.metrics import MetricsRegistry
from app.request_context import reset_request_id, set_request_id

LEGACY_ENDPOINTS = {
    "/move",
    "/reference",
    "/fault_reset",
    "/position",
    "/is_motion",
    "/status",
}


async def request_id_middleware(
    request: Request,
    call_next,
    *,
    metrics: MetricsRegistry,
) -> Response:
    """Inject ``X-Request-ID``, apply legacy deprecation headers, record latency."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    token = set_request_id(request_id)
    started = time.monotonic()
    logger = logging.getLogger("http.request")
    try:
        is_legacy_path = request.url.path in LEGACY_ENDPOINTS
        legacy_phase = str(
            getattr(app_config, "LEGACY_API_PHASE", "deprecated") or "deprecated"
        ).lower()
        if is_legacy_path and legacy_phase not in {"deprecated", "sunset", "removed"}:
            legacy_phase = "deprecated"

        if is_legacy_path:
            metrics.observe_legacy_api_request(path=request.url.path, phase=legacy_phase)

        if is_legacy_path and legacy_phase == "removed":
            payload = error_detail(
                code=error_codes.LEGACY_API_REMOVED.code,
                message=error_codes.LEGACY_API_REMOVED.message,
                details={
                    "request_id": request_id,
                    "successor_path": app_config.LEGACY_API_SUCCESSOR_PATH,
                },
            )
            return JSONResponse(
                status_code=410,
                content=payload,
                headers={"X-Request-ID": request_id, "X-API-Phase": "removed"},
            )

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if app_config.LEGACY_API_DEPRECATION and is_legacy_path:
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = app_config.LEGACY_API_SUNSET
            response.headers["Link"] = (
                f'<{app_config.LEGACY_API_DOCS_LINK}>; rel="alternate", '
                f'<{app_config.LEGACY_API_SUCCESSOR_PATH}>; rel="successor-version"'
            )
            response.headers["X-API-Phase"] = legacy_phase
        elapsed_ms = (time.monotonic() - started) * 1000.0
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
