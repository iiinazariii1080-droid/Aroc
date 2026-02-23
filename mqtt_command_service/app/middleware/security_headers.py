"""Security headers middleware.

Adds standard security headers to all HTTP responses to protect
against common web vulnerabilities (clickjacking, MIME sniffing,
XSS, etc.).
"""
from typing import Any

from fastapi import Request, Response


async def add_security_headers_middleware(request: Request, call_next: Any) -> Response:
    """Add security headers to every response."""
    response: Response = await call_next(request)

    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"

    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # XSS protection (legacy browsers)
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Referrer policy — avoid leaking full URLs to third parties
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions policy — disable unnecessary browser features
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )

    # Content-Security-Policy — relaxed for Swagger UI / ReDoc pages,
    # strict for all other (API) endpoints.
    path = request.url.path
    if path in ("/docs", "/redoc", "/openapi.json") or path.startswith("/docs/") or path.startswith("/redoc/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' https://fastapi.tiangolo.com data:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )

    # Cache control — prevent caching of API responses
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"

    return response
