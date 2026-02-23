"""FastAPI exception handlers — consistent JSON error envelopes."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import error_codes
from app.http_errors import error_detail, normalize_error_detail
from app.metrics import MetricsRegistry


def register_exception_handlers(app, *, metrics: MetricsRegistry) -> None:
    """Attach all exception handlers to *app*."""

    def _handle_http_exc(request: Request, exc: HTTPException | StarletteHTTPException) -> JSONResponse:
        """Shared logic for FastAPI and Starlette HTTP exceptions."""
        payload = normalize_error_detail(exc.status_code, exc.detail)
        error_code = str(payload.get("code", "REQUEST_ERROR"))
        metrics.observe_error(
            method=request.method,
            path=request.url.path,
            status_code=exc.status_code,
            code=error_code,
        )
        details = payload.get("details")
        if details is None or not isinstance(details, dict):
            details = {}
        details["request_id"] = getattr(request.state, "request_id", None)
        payload["details"] = details
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return _handle_http_exc(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return _handle_http_exc(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        metrics.observe_error(
            method=request.method,
            path=request.url.path,
            status_code=422,
            code=error_codes.VALIDATION_ERROR.code,
        )
        payload = error_detail(
            code=error_codes.VALIDATION_ERROR.code,
            message=error_codes.VALIDATION_ERROR.message,
            details={
                "errors": exc.errors(),
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled request exception: %s", exc)
        metrics.observe_error(
            method=request.method,
            path=request.url.path,
            status_code=500,
            code=error_codes.INTERNAL_ERROR.code,
        )
        payload = error_detail(
            code=error_codes.INTERNAL_ERROR.code,
            message=error_codes.INTERNAL_ERROR.message,
            details={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(status_code=500, content=payload)
