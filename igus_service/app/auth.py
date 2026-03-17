"""API key authentication for motion-critical endpoints.

Secrets management:
    The API key is read from the ``IGUS_API_KEY`` environment variable.
    For container deployments this is the standard mechanism (Docker secrets,
    Kubernetes Secrets mounted as env vars, docker-compose env_file).

    For production / industrial-grade deployments consider:
    - Docker Swarm secrets (mounted at /run/secrets/)
    - Kubernetes Secrets with volume mount (not env vars)
    - HashiCorp Vault with sidecar injector
    - AWS Secrets Manager / Azure Key Vault via init container

    The env var approach is acceptable when the orchestrator manages secret
    injection (e.g. ``docker run --env-file .env.secret``).  Avoid committing
    ``.env`` files with real keys to version control — use ``.env.example``
    with placeholder values instead.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


# Cache API key at module load time.  The value cannot change at runtime
# (process does not re-read env vars), so reading once avoids both the
# per-request overhead and the false impression that hot-reload is possible.
_CACHED_API_KEY: str | None = None
_API_KEY_LOADED: bool = False


def is_auth_disabled() -> bool:
    """Return True when auth is explicitly disabled via ``IGUS_AUTH_DISABLED=true``.

    This is the opt-out mechanism for development / test environments.
    Production deployments MUST set ``IGUS_API_KEY`` instead.
    """
    return os.getenv("IGUS_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


def get_api_key() -> str | None:
    """Return the configured API key, or None if auth is disabled.

    Auth is **required by default**.  To run without authentication you must
    either set ``IGUS_API_KEY`` (recommended) or explicitly opt out by setting
    ``IGUS_AUTH_DISABLED=true`` (development only).

    The value is cached after the first call.
    """
    global _CACHED_API_KEY, _API_KEY_LOADED  # noqa: PLW0603
    if not _API_KEY_LOADED:
        if is_auth_disabled():
            _CACHED_API_KEY = None
        else:
            key = os.getenv("IGUS_API_KEY", "").strip()
            _CACHED_API_KEY = key if key else None
        _API_KEY_LOADED = True
    return _CACHED_API_KEY


async def require_api_key(request: Request) -> None:
    """FastAPI dependency that enforces API key authentication.

    Use as ``Depends(require_api_key)`` on protected routes.
    When ``IGUS_API_KEY`` is set, requests must include a matching
    ``X-API-Key`` header.  Raises 401 on missing key, 403 on wrong key.
    When no API key is configured (or auth is disabled), passes through.
    """
    expected_key = get_api_key()
    if expected_key is None:
        return  # auth not configured — pass through

    provided = request.headers.get("X-API-Key", "").strip()
    if not provided:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "AUTH_REQUIRED",
                "message": "X-API-Key header is required for this endpoint",
            },
        )
    if not hmac.compare_digest(provided, expected_key):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_FORBIDDEN",
                "message": "Invalid API key",
            },
        )
