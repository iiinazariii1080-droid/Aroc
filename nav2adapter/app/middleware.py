"""FastAPI middleware for request tracing."""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.request_context import set_request_id, _request_id_var


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or propagate ``X-Request-ID`` and store in contextvars."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = set_request_id(rid)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _request_id_var.reset(token)
