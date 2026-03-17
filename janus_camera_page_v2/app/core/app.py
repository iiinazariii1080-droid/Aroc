import asyncio
import collections
import contextvars
import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.events import register_event_handlers
from app.core.settings import get_settings
from app.routes import register_routes

_log = logging.getLogger(__name__)

# ── Request ID propagation via contextvars ────────────────────────────
# Set by RequestTracingMiddleware, read by RequestIdFilter on every log record.
_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None,
)


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` from the current context into every log record.

    The JsonFormatter already checks ``getattr(record, 'request_id', None)``
    — this filter bridges the gap by reading the ContextVar set by
    RequestTracingMiddleware.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()  # type: ignore[attr-defined]
        return True


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request/response for cross-node correlation."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # Store on request.state so downstream handlers / logging can access it.
        request.state.request_id = request_id
        # Propagate into contextvars so the logging filter picks it up for
        # every log record emitted during this request.
        token = _request_id_ctx.set(request_id)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_id_ctx.reset(token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window in-process rate limiter for high-traffic endpoints.

    Security:
      - X-Forwarded-For is only trusted when the direct peer IP is in
        ``trusted_proxies``.  Otherwise the peer IP is used directly,
        preventing spoofed headers from bypassing rate limits.
      - ``max_buckets`` caps total tracked (path, IP) pairs to prevent
        memory exhaustion from IP spoofing attacks.

    Paths matched (prefix):
      /snapshot.jpg  — burst protection for MJPEG-style polling clients
      /healthz       — prevent scrapers from drowning health probes
      /janus/ws      — WebSocket upgrade flood protection

    Returns 429 with ``Retry-After`` and ``X-RateLimit-*`` headers when a
    client exceeds its budget.  All limits are configurable via Settings.
    """

    def __init__(self, app, *, settings) -> None:
        super().__init__(app)
        self._enabled = settings.rate_limit_enabled
        self._window = settings.rate_limit_window_sec
        self._max_buckets = settings.rate_limit_max_buckets
        self._trusted_proxies: frozenset[str] = frozenset(
            ip.strip() for ip in settings.trusted_proxies.split(",") if ip.strip()
        )
        self._routes: list[tuple[str, int]] = [
            ("/snapshot.jpg", settings.rate_limit_snapshot),
            ("/healthz", settings.rate_limit_healthz),
            ("/janus/ws", settings.rate_limit_janus_ws),
        ]
        # {(path_prefix, client_ip): deque[float]}
        self._buckets: dict[tuple[str, str], collections.deque] = {}
        self._lock = asyncio.Lock()

    def _client_ip(self, request: Request) -> str:
        """Extract client IP, trusting X-Forwarded-For only from trusted proxies."""
        peer_ip = request.client.host if request.client else "unknown"
        if peer_ip in self._trusted_proxies:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer_ip

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        for prefix, limit in self._routes:
            if path == prefix or path.startswith(prefix + "?"):
                ip = self._client_ip(request)
                key = (prefix, ip)
                now = time.monotonic()
                cutoff = now - self._window

                async with self._lock:
                    bucket = self._buckets.setdefault(key, collections.deque())
                    # Drain expired entries.
                    while bucket and bucket[0] <= cutoff:
                        bucket.popleft()
                    count = len(bucket)
                    if count >= limit:
                        # bucket[0] is the oldest unexpired timestamp.  When it
                        # is close to the cutoff edge, (now - bucket[0]) ≈ window
                        # and retry_after collapses to 1 s — semantically correct
                        # ("try again shortly").
                        retry_after = max(1, int(self._window - (now - bucket[0])) + 1)
                        return Response(
                            content="Too Many Requests",
                            status_code=429,
                            headers={
                                "Retry-After": str(retry_after),
                                "X-RateLimit-Limit": str(limit),
                                "X-RateLimit-Remaining": "0",
                                "X-RateLimit-Window": str(int(self._window)),
                            },
                        )
                    bucket.append(now)
                    # No need to re-insert — setdefault already put bucket
                    # in _buckets; only evict truly dead keys after the
                    # max_buckets overflow check below.

                    # Memory bound: evict stale buckets when over capacity.
                    if len(self._buckets) > self._max_buckets:
                        stale_keys = [
                            k for k, dq in self._buckets.items()
                            if not dq or dq[-1] <= cutoff
                        ]
                        for k in stale_keys:
                            del self._buckets[k]
                        # If still over, evict oldest-accessed buckets.
                        while len(self._buckets) > self._max_buckets:
                            oldest = min(
                                self._buckets,
                                key=lambda k: self._buckets[k][-1] if self._buckets[k] else 0,
                            )
                            del self._buckets[oldest]

                    from app.metrics.safe import safe_set
                    safe_set("ratelimit_tracked_buckets", len(self._buckets))
                break  # only match the first applicable rule

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response (P2.9)."""

    def __init__(self, app, *, frame_ancestors_lan: str = "") -> None:
        super().__init__(app)
        self._frame_ancestors_lan = frame_ancestors_lan

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            # unsafe-inline required: Janus player UI uses inline styles for
            # video element sizing and layout that cannot be extracted to static CSS.
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' wss: ws: https://*.techvisioncloud.pl; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            f"frame-ancestors 'self' https://*.techvisioncloud.pl {self._frame_ancestors_lan}"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=()"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_title, version=settings.app_version)

    # Install request-ID logging filter on the root logger so every record
    # emitted during a request carries the request_id for JSON log correlation.
    logging.getLogger().addFilter(RequestIdFilter())

    application.add_middleware(SecurityHeadersMiddleware, frame_ancestors_lan=settings.csp_frame_ancestors_lan)
    application.add_middleware(RequestTracingMiddleware)
    application.add_middleware(RateLimitMiddleware, settings=settings)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
    )

    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    register_routes(application)
    register_event_handlers(application)
    return application
