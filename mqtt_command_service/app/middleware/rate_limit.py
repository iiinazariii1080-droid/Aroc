"""Rate limiting middleware."""

from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Global limiter instance
_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """Get or create limiter instance."""
    global _limiter
    if _limiter is None:
        _limiter = Limiter(key_func=get_remote_address)
    return _limiter


def setup_rate_limiting(app: FastAPI) -> Limiter:
    """Setup rate limiting for FastAPI app."""
    limiter = get_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    return limiter

