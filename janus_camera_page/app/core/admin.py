from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN = os.getenv("CAM_ADMIN_TOKEN", "change-me")
_ENFORCE = os.getenv("CAM_ADMIN_ENFORCE", "1") == "1"

logger = logging.getLogger("admin")


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
    supplied = request.headers.get("X-Admin-Token", "")

    if _ENFORCE:
        if ADMIN_TOKEN == "change-me":
            raise HTTPException(
                status_code=503,
                detail="Admin endpoint disabled: CAM_ADMIN_TOKEN is still "
                       "the default placeholder. Set a strong token in "
                       "camera-secrets.env before using admin routes.",
            )
        if supplied != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid admin token")
    else:
        if supplied and supplied != ADMIN_TOKEN:
            logger.warning("Admin token mismatch (enforcement disabled)")

    request.state.admin_token = ADMIN_TOKEN
    return None

