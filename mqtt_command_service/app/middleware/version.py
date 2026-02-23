"""API versioning middleware."""
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from app.core.config import settings


async def add_api_version_middleware(request: Request, call_next: Any) -> Response:
    """Add API version header to responses."""
    response: Response = await call_next(request)

    # Add API version header
    response.headers["API-Version"] = settings.API_VERSION

    return response

