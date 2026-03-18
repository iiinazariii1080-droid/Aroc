"""HTTP-based auth adapter that calls the hub-auth microservice.

Satisfies AuthManagerProtocol by proxying to the hub-auth REST API
(GET /auth/headers, GET /auth/status).  This completes the integration
that was declared in docker-compose but never wired up.
"""

import contextlib
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Cache auth status for a short period to avoid hammering hub-auth on every request.
_STATUS_CACHE_TTL = 10.0
# Shorter TTL for failed responses — prevents hammering a down hub-auth
# while still retrying reasonably quickly after recovery.
_NEGATIVE_CACHE_TTL = 5.0
# Max age for cached auth headers before clearing — prevents returning
# expired JWTs indefinitely when hub-auth is unreachable.
_MAX_CACHE_AGE = 3600.0


class HubAuthHttpAdapter:
    """AuthManagerProtocol implementation that delegates to the hub-auth HTTP service."""

    def __init__(self, hub_auth_url: str, timeout: float = 5.0, internal_service_key: str = "") -> None:
        self._base_url = hub_auth_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        if internal_service_key:
            self._session.headers["X-Internal-Key"] = internal_service_key
        ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE")
        if ca_bundle:
            self._session.verify = ca_bundle
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._cached_status: dict | None = None
        self._cached_status_time: float = 0.0
        self._cached_headers: dict[str, str] = {}
        self._cached_headers_time: float = 0.0

    def auth_headers(self) -> dict[str, str]:
        """GET /auth/headers → {"Authorization": "Bearer <jwt>"} or {}.

        Uses double-checked locking: the cache lock is held only briefly
        for reading/writing cached values.  The HTTP call runs outside
        any lock so that slow/failing hub-auth does not block all threads.
        """
        now = time.time()
        with self._lock:
            if now - self._cached_headers_time < _STATUS_CACHE_TTL:
                return dict(self._cached_headers)

        with self._refresh_lock:
            # Re-check: another thread may have refreshed while we waited.
            with self._lock:
                if time.time() - self._cached_headers_time < _STATUS_CACHE_TTL:
                    return dict(self._cached_headers)

            # HTTP call outside lock — may block up to self._timeout.
            try:
                resp = self._session.get(
                    f"{self._base_url}/auth/headers",
                    timeout=self._timeout,
                )
                if resp.ok:
                    data = resp.json()
                    if not isinstance(data, dict):
                        logger.warning("[hub-auth-adapter] /auth/headers returned non-dict: %s", type(data).__name__)
                        with self._lock:
                            self._cached_headers_time = time.time() - _STATUS_CACHE_TTL + _NEGATIVE_CACHE_TTL
                    else:
                        with self._lock:
                            self._cached_headers = data
                            self._cached_headers_time = time.time()
                        return data
                else:
                    logger.warning("[hub-auth-adapter] /auth/headers returned %s", resp.status_code)
                    with self._lock:
                        self._cached_headers_time = time.time() - _STATUS_CACHE_TTL + _NEGATIVE_CACHE_TTL
            except Exception:
                logger.warning("[hub-auth-adapter] Failed to fetch auth headers", exc_info=True)
                with self._lock:
                    self._cached_headers_time = time.time() - _STATUS_CACHE_TTL + _NEGATIVE_CACHE_TTL

        with self._lock:
            age = time.time() - self._cached_headers_time if self._cached_headers_time else float("inf")
            if age > _MAX_CACHE_AGE:
                self._cached_headers = {}
                logger.warning("[hub-auth-adapter] Cached auth headers expired (age=%.0fs) — clearing", age)
            headers = dict(self._cached_headers)
        if not headers:
            logger.warning("[hub-auth-adapter] Returning empty auth headers — hub-auth may be unreachable")
        else:
            logger.warning("[hub-auth-adapter] Returning stale cached auth headers (age=%.0fs)", age)
        return headers

    def robot_id(self) -> str | None:
        """Extract robot_id from cached /auth/status response."""
        status = self._get_status()
        return status.get("robot_id") if status else None

    def has_valid_token(self) -> bool:
        """Check if hub-auth reports a valid token."""
        status = self._get_status()
        return bool(status.get("valid")) if status else False

    def close(self) -> None:
        """Close the HTTP session."""
        with contextlib.suppress(Exception):
            self._session.close()

    def _get_status(self) -> dict | None:
        """GET /auth/status with short TTL cache.

        Uses double-checked locking (same pattern as auth_headers) to
        prevent concurrent callers from issuing redundant HTTP requests.
        """
        with self._lock:
            if self._cached_status is not None and time.time() - self._cached_status_time < _STATUS_CACHE_TTL:
                return self._cached_status

        with self._refresh_lock:
            # Re-check: another thread may have refreshed while we waited.
            with self._lock:
                if self._cached_status is not None and time.time() - self._cached_status_time < _STATUS_CACHE_TTL:
                    return self._cached_status

            try:
                resp = self._session.get(
                    f"{self._base_url}/auth/status",
                    timeout=self._timeout,
                )
                if resp.ok:
                    data = resp.json()
                    if isinstance(data, dict):
                        with self._lock:
                            self._cached_status = data
                            self._cached_status_time = time.time()
                        return data
                    logger.warning("[hub-auth-adapter] /auth/status returned non-dict: %s", type(data).__name__)
                with self._lock:
                    self._cached_status_time = time.time() - _STATUS_CACHE_TTL + _NEGATIVE_CACHE_TTL
            except Exception:
                logger.debug("[hub-auth-adapter] Failed to fetch auth status", exc_info=True)
                with self._lock:
                    self._cached_status_time = time.time() - _STATUS_CACHE_TTL + _NEGATIVE_CACHE_TTL
        with self._lock:
            return self._cached_status
