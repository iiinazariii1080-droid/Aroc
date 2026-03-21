"""Lightweight per-IP token bucket rate limiter for FastAPI.

Designed for RPi5 — no external dependencies, minimal overhead.
Usage: add ``Depends(require_rate_limit)`` to routes that need protection.
       add ``Depends(require_admin_rate_limit)`` to admin routes (stricter).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict

from fastapi import HTTPException, Request
from prometheus_client import Counter

logger = logging.getLogger("rate_limit")

# Canonical counter — defined here (not in app.metrics) to avoid circular imports.
admin_rate_limit_exceeded_total = Counter(
    "camstack_admin_rate_limit_exceeded_total",
    "Admin endpoint rate-limit 429 responses (potential bruteforce)",
)

# Configurable via env
_RATE = float(os.getenv("RATE_LIMIT_RPS", "30"))       # tokens per second
_BURST = float(os.getenv("RATE_LIMIT_BURST", "60"))     # max burst
_CLEANUP_SEC = 60.0                                      # stale entry TTL

# Admin rate limit: 5 requests per minute (brute-force protection)
_ADMIN_RATE = float(os.getenv("ADMIN_RATE_LIMIT_RPM", "5")) / 60.0  # tokens/sec
_ADMIN_BURST = float(os.getenv("ADMIN_RATE_LIMIT_BURST", "5"))

_TRUSTED_PROXIES = frozenset(
    p.strip() for p in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if p.strip()
)


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from trusted proxies."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip in _TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return client_ip


class _Bucket:
    __slots__ = ("tokens", "last", "_rate", "_burst")

    def __init__(self, rate: float = _RATE, burst: float = _BURST) -> None:
        self._rate = rate
        self._burst = burst
        self.tokens = burst
        self.last = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last
        self.last = now
        self.tokens = min(self._burst, self.tokens + elapsed * self._rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


_buckets: Dict[str, _Bucket] = {}
_lock = asyncio.Lock()
_last_cleanup = time.monotonic()


async def require_rate_limit(request: Request) -> None:
    """FastAPI dependency — raises 429 if rate exceeded."""
    global _last_cleanup
    client_ip = _get_client_ip(request)

    async with _lock:
        # Periodic cleanup of stale entries
        now = time.monotonic()
        if now - _last_cleanup > _CLEANUP_SEC:
            stale = [k for k, v in _buckets.items() if (now - v.last) > _CLEANUP_SEC]
            for k in stale:
                del _buckets[k]
            _last_cleanup = now

        bucket = _buckets.get(client_ip)
        if bucket is None:
            bucket = _Bucket()
            _buckets[client_ip] = bucket

        if not bucket.consume():
            logger.warning("Rate limit exceeded for %s", client_ip)
            raise HTTPException(status_code=429, detail="Too many requests")


# ── Admin-specific rate limiter (stricter, separate buckets) ──────────

_admin_buckets: Dict[str, _Bucket] = {}
_admin_lock = asyncio.Lock()
_admin_last_cleanup = time.monotonic()


async def require_admin_rate_limit(request: Request) -> None:
    """Stricter rate limit for admin endpoints (brute-force protection).

    Defaults to 5 req/min per IP — separate from the general rate limiter.
    """
    global _admin_last_cleanup
    client_ip = _get_client_ip(request)

    async with _admin_lock:
        now = time.monotonic()
        if now - _admin_last_cleanup > _CLEANUP_SEC:
            stale = [k for k, v in _admin_buckets.items() if (now - v.last) > _CLEANUP_SEC]
            for k in stale:
                del _admin_buckets[k]
            _admin_last_cleanup = now

        bucket = _admin_buckets.get(client_ip)
        if bucket is None:
            bucket = _Bucket(rate=_ADMIN_RATE, burst=_ADMIN_BURST)
            _admin_buckets[client_ip] = bucket

        if not bucket.consume():
            logger.warning("Admin rate limit exceeded for %s", client_ip)
            admin_rate_limit_exceeded_total.inc()
            raise HTTPException(status_code=429, detail="Too many requests")
