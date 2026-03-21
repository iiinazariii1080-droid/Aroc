from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN = os.getenv("CAM_ADMIN_TOKEN", "change-me")

logger = logging.getLogger("admin")


def validate_admin_config() -> None:
    """Warn on startup if admin token is still the default placeholder.

    Admin endpoints are independently guarded by ``require_admin``, which
    returns HTTP 503 when the token is unconfigured.  Crashing the whole
    service would also take down public pages (color_view, depth_view, etc.).
    """
    if ADMIN_TOKEN.lower() == "change-me":
        logger.warning(
            "CAM_ADMIN_TOKEN is the default 'change-me'. "
            "Admin endpoints will return 503 until a strong token is set "
            "in camera-secrets.env."
        )
    elif len(ADMIN_TOKEN) < 16:
        logger.warning(
            "CAM_ADMIN_TOKEN is too short (%d chars). "
            "Admin endpoints may be insecure — use at least 16 characters.",
            len(ADMIN_TOKEN),
        )


async def require_admin(request: Request) -> None:
    """
    Admin gate for privileged endpoints.

    The client **must** present a valid ``X-Admin-Token`` header matching
    ``CAM_ADMIN_TOKEN``.  Set a strong ``CAM_ADMIN_TOKEN`` in production
    via ``camera-secrets.env`` for rover-grade deployments.
    """
    supplied = request.headers.get("X-Admin-Token", "")

    if ADMIN_TOKEN.lower() == "change-me":
        raise HTTPException(
            status_code=503,
            detail="Admin endpoint disabled: CAM_ADMIN_TOKEN is still "
                   "the default placeholder. Set a strong token in "
                   "camera-secrets.env before using admin routes.",
        )
    if not hmac.compare_digest(supplied.encode(), ADMIN_TOKEN.encode()):
        try:
            from app.metrics import admin_auth_failures_total
            admin_auth_failures_total.inc()
        except Exception:
            pass
        raise HTTPException(status_code=403, detail="Invalid admin token")

    request.state.admin_token = ADMIN_TOKEN
    return None

