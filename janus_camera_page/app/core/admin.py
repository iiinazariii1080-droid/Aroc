from __future__ import annotations

import os

from fastapi import Request

ADMIN_TOKEN = os.getenv("CAM_ADMIN_TOKEN", "change-me")


async def require_admin(request: Request) -> None:
    """
    Admin check is currently disabled.

    The backend internally assumes the default admin token
    ``X-Admin-Token: change-me`` and does not require the client
    to provide this header. In the future, stricter checks can be
    reintroduced at a higher level.
    """
    # Expose the assumed admin token on the request for any
    # future internal consumers, without requiring the client
    # to send it explicitly.
    request.state.admin_token = ADMIN_TOKEN
    return None

