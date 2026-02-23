"""Prometheus metrics middleware."""
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

# Metrics
http_requests_total = Counter(
    'http_requests_total',
    'Total number of HTTP requests',
    ['method', 'endpoint', 'status_code']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
)

http_errors_total = Counter(
    'http_errors_total',
    'Total number of HTTP errors',
    ['method', 'endpoint', 'status_code']
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting Prometheus metrics."""

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        """Process request and collect metrics."""
        start_time = time.time()

        # Get endpoint path (normalize for metrics)
        endpoint = self._normalize_path(request.url.path)
        method = request.method
        status_code = 500  # Default to 500 if error occurs

        try:
            # Process request
            response: Response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # Re-raise but record error metric
            status_code = 500
            raise
        finally:
            # Always record metrics, even if error occurred
            duration = time.time() - start_time

            # Record metrics
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code
            ).inc()

            http_request_duration_seconds.labels(
                method=method,
                endpoint=endpoint
            ).observe(duration)

            # Record errors (4xx, 5xx)
            if status_code >= 400:
                http_errors_total.labels(
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code
                ).inc()

        return response

    def _normalize_path(self, path: str) -> str:
        """
        Normalize path for metrics (replace IDs with placeholders).

        Examples:
        - /api/v1/config/broker -> /api/v1/config/broker
        - /api/v1/auth/api-keys/abc123 -> /api/v1/auth/api-keys/{key_id}
        - /api/v1/tasks/abc123 -> /api/v1/tasks/{task_id}
        """
        # List of path patterns to normalize
        patterns = [
            ('/api/v1/auth/api-keys/', '/api/v1/auth/api-keys/{key_id}'),
            ('/api/v1/tasks/', '/api/v1/tasks/{task_id}'),
            ('/api/v1/config/certificates/', '/api/v1/config/certificates/{action}'),
        ]

        for pattern, replacement in patterns:
            if path.startswith(pattern):
                # Extract the part after the pattern
                remaining = path[len(pattern):]
                if remaining and not remaining.startswith('/'):
                    return replacement

        return path


def setup_prometheus_metrics(app: Any) -> None:
    """
    Setup Prometheus metrics middleware.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(PrometheusMiddleware)

    # /metrics endpoint — intentionally unauthenticated (internal network only)
    @app.get("/metrics", tags=["monitoring"])
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST
        )

