from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN = os.getenv("CAM_ADMIN_TOKEN", "change-me")
_ENFORCE = os.getenv("CAM_ADMIN_ENFORCE", "0") == "1"

logger = logging.getLogger("admin")


async def require_admin(request: Request) -> None:
    """
    Admin gate for privileged endpoints.

    When ``CAM_ADMIN_ENFORCE=1`` the client **must** present a valid
    ``X-Admin-Token`` header matching ``CAM_ADMIN_TOKEN``.
    When enforcement is off (default for backward compatibility), the
    default token is injected into ``request.state`` but the check is
    permissive — a warning is logged instead.

    Set ``CAM_ADMIN_ENFORCE=1`` and a strong ``CAM_ADMIN_TOKEN`` in
    production for rover-grade deployments.
    """
    supplied = request.headers.get("X-Admin-Token", "")

    if _ENFORCE:
        if ADMIN_TOKEN == "change-me":
            logger.error(
                "CAM_ADMIN_ENFORCE=1 but CAM_ADMIN_TOKEN is the default. "
                "Set a strong token before enabling enforcement."
            )
        if supplied != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid admin token")
    else:
        if supplied and supplied != ADMIN_TOKEN:
            logger.warning("Admin token mismatch (enforcement disabled)")

    request.state.admin_token = ADMIN_TOKEN
    return None

