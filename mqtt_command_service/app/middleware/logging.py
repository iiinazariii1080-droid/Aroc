"""Request logging middleware."""
import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import HTTPException, Request, Response, status

from app.core.security import (
    API_KEY_HEADER,
    AUTH_DISABLED,
    Role,
    is_optional_auth_endpoint,
    is_public_endpoint,
    verify_api_key,
)

logger = logging.getLogger(__name__)


async def log_requests_middleware(request: Request, call_next: Any) -> Response:
    """Log API requests with authentication info and brute force protection."""
    start_time = time.time()

    # Generate correlation ID
    correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Add correlation ID to request state
    request.state.correlation_id = correlation_id

    # Skip for public endpoints
    if is_public_endpoint(request.url.path):
        response: Response = await call_next(request)
        # Add correlation ID to response headers
        response.headers["X-Request-ID"] = correlation_id
        return response

    # Get client IP for brute force protection
    client_ip = None
    if hasattr(request, 'client') and request.client:
        client_ip = request.client.host

    role: Role | None = None
    api_key = request.headers.get(API_KEY_HEADER)

    if AUTH_DISABLED:
        role = Role.ADMIN
    elif is_optional_auth_endpoint(request.url.path):
        # Optional auth: allow missing key, validate if provided
        if not api_key:
            role = Role.ADMIN
        else:
            role = await asyncio.to_thread(verify_api_key, api_key, client_ip)
            if role is None:
                from fastapi.responses import JSONResponse
                response = JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or missing API key"}
                )
                response.headers["X-Request-ID"] = correlation_id
                return response
    else:
        # Strict auth for protected endpoints
        try:
            role = await asyncio.to_thread(verify_api_key, api_key, client_ip)
        except HTTPException as e:
            if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                logger.warning(
                    "Brute force protection triggered for IP %s: %s",
                    client_ip, e.detail,
                    extra={
                        "request_id": correlation_id,
                        "ip": client_ip,
                        "path": request.url.path,
                        "method": request.method
                    }
                )
                from fastapi.responses import JSONResponse
                response = JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": e.detail}
                )
                response.headers["X-Request-ID"] = correlation_id
                return response
            raise

    # Store verified role in request state for downstream dependencies.
    # This is the SINGLE verification point — require_auth() reads this value
    # instead of re-calling verify_api_key(), eliminating double DB lookups.
    request.state.auth_role = role

    response = await call_next(request)

    process_time = time.time() - start_time

    # Log with structured data
    logger.info(
        "API request: %s %s status=%s role=%s ip=%s time=%.3fs",
        request.method, request.url.path, response.status_code,
        role.value if role else "unauthorized",
        client_ip or "unknown", process_time,
        extra={
            "request_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration": process_time,
            "ip": client_ip,
            "user": role.value if role else "unauthorized"
        }
    )

    # Add correlation ID to response headers
    response.headers["X-Request-ID"] = correlation_id

    return response

