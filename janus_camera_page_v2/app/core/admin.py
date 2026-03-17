from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

# Emit a one-time startup warning if admin enforcement is disabled so
# operators notice the misconfiguration before it reaches production.
if not get_settings().admin_enforce:
    logger.warning(
        "CAM_ADMIN_ENFORCE=0 — admin authentication is DISABLED. "
        "All admin endpoints (restart, config, NAT) are publicly accessible. "
        "This should only be used for local development."
    )


async def require_admin(request: Request) -> None:
    """
    Admin gate for privileged endpoints.

    When ``CAM_ADMIN_ENFORCE=1`` (default) the client **must** present a
    valid ``X-Admin-Token`` header matching ``CAM_ADMIN_TOKEN``.
    Set ``CAM_ADMIN_ENFORCE=0`` to disable enforcement for local
    development only.

    Set a strong ``CAM_ADMIN_TOKEN`` in production via
    ``camera-secrets.env`` for rover-grade deployments.
    """
    settings = get_settings()
    supplied = request.headers.get("X-Admin-Token", "")

    if settings.admin_enforce:
        if not settings.admin_token:
            raise HTTPException(
                status_code=503,
                detail="Admin endpoint disabled: CAM_ADMIN_TOKEN is not set. "
                       "Set a strong token in camera-secrets.env before "
                       "using admin routes.",
            )
        if not hmac.compare_digest(supplied, settings.admin_token):
            raise HTTPException(status_code=403, detail="Invalid admin token")
    else:
        if supplied and not hmac.compare_digest(supplied, settings.admin_token):
            logger.warning("Admin token mismatch (enforcement disabled)")
