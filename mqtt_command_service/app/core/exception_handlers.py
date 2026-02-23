"""Exception handlers for FastAPI."""
import logging
from typing import Union

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.exceptions import BaseAPIException

logger = logging.getLogger(__name__)


async def base_api_exception_handler(
    request: Request,
    exc: BaseAPIException
) -> JSONResponse:
    """Handle custom API exceptions."""
    logger.warning(
        "API error: %s - %s (path: %s, method: %s)",
        exc.error_code, exc.detail, request.url.path, request.method,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.error_code,
                "message": exc.detail,
                "type": exc.__class__.__name__
            }
        }
    )


async def validation_exception_handler(
    request: Request,
    exc: Union[RequestValidationError, ValidationError]
) -> JSONResponse:
    """Handle Pydantic validation errors."""
    errors = exc.errors() if hasattr(exc, 'errors') else []

    logger.debug(
        "Validation error: %s (path: %s, method: %s)",
        errors, request.url.path, request.method,
    )

    # Format validation errors
    formatted_errors = []
    for error in errors:
        field = ".".join(str(loc) for loc in error.get("loc", []))
        formatted_errors.append({
            "field": field,
            "message": error.get("msg", "Validation error"),
            "type": error.get("type", "validation_error")
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": formatted_errors,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "type": "ValidationError",
                "details": formatted_errors
            }
        }
    )


async def general_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.error(
        "Unexpected error: %s - %s (path: %s, method: %s)",
        exc.__class__.__name__, str(exc), request.url.path, request.method,
        exc_info=True,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "type": "Exception"
            }
        }
    )

