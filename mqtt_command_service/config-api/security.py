"""API key authentication middleware for config-api.

Thin wrapper — delegates to rbac, hmac_keys, brute_force, api_key_store,
and settings_lazy modules.
"""

import hmac
import logging
from typing import Any

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from prometheus_client import Counter

from api_key_store import get_api_key_role, is_api_key_revoked
from brute_force import check_lockout, clear_failed_attempts, record_failed_attempt
from hmac_keys import hash_api_key
from rbac import ROLE_HIERARCHY, Role
from settings_lazy import (
    get_allow_unauthenticated_read,
    get_auth_disabled,
    get_emergency_api_key,
    get_internal_service_key,
)

logger = logging.getLogger(__name__)

auth_bypassed_requests_total = Counter(
    "auth_bypassed_requests_total",
    "Requests served with auth disabled (AUTH_DISABLED=true)",
)

API_KEY_HEADER = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# ---------------------------------------------------------------------------
# Re-exports (public API surface)
# ---------------------------------------------------------------------------
from api_key_store import create_api_key, revoke_api_key  # noqa: F401
from brute_force import LOCKOUT_DURATION, MAX_FAILED_ATTEMPTS  # noqa: F401
from hmac_keys import generate_api_key  # noqa: F401


def verify_api_key(api_key: str | None, client_ip: str | None = None) -> Role | None:
    if client_ip:
        if check_lockout(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed authentication attempts."
            )

    if not api_key:
        # No API key supplied — return None without recording a failed
        # attempt.  Recording here would let a bot-scanner without any
        # key lock out legitimate users behind the same NAT/IP.
        return None

    emergency_key = get_emergency_api_key()
    if emergency_key and hmac.compare_digest(api_key, emergency_key):
        clear_failed_attempts(client_ip)
        return Role.ADMIN

    internal_key = get_internal_service_key()
    if internal_key and hmac.compare_digest(api_key, internal_key):
        clear_failed_attempts(client_ip)
        return Role.SERVICE

    if not (20 <= len(api_key) <= 100):
        record_failed_attempt(client_ip)
        return None

    key_hash = hash_api_key(api_key)

    if is_api_key_revoked(key_hash):
        record_failed_attempt(client_ip)
        return None

    role = get_api_key_role(key_hash)
    if role:
        clear_failed_attempts(client_ip)
        return role

    record_failed_attempt(client_ip)
    return None


def require_auth(required_role: Role = Role.READ) -> Any:
    async def auth_dependency(
        request: Request,
        api_key: str | None = Security(api_key_header),
    ) -> Role:
        if get_auth_disabled():
            auth_bypassed_requests_total.inc()
            return Role.ADMIN

        role = getattr(request.state, "auth_role", None)
        if role is None and api_key:
            client_ip = request.client.host if request.client else None
            role = verify_api_key(api_key, client_ip=client_ip)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[required_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {required_role.value}, your role: {role.value}",
            )

        return role

    return auth_dependency


def optional_auth() -> Any:
    async def auth_dependency(
        request: Request,
        api_key: str | None = Security(api_key_header),
    ) -> Role:
        if get_auth_disabled():
            auth_bypassed_requests_total.inc()
            return Role.ADMIN

        if not api_key:
            if get_allow_unauthenticated_read():
                logger.debug(
                    "Unauthenticated READ access granted (ALLOW_UNAUTHENTICATED_READ=true) from %s",
                    request.client.host if request.client else "unknown",
                )
                return Role.READ
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key required. Set ALLOW_UNAUTHENTICATED_READ=true or provide an X-API-Key header.",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        role = getattr(request.state, "auth_role", None)
        if role is None:
            client_ip = request.client.host if request.client else None
            role = verify_api_key(api_key, client_ip=client_ip)

        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return role

    return auth_dependency
