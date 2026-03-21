from __future__ import annotations

import json
import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.core.settings import get_settings
from app.services.proxy_base import AsyncHttpProxy

log = logging.getLogger(__name__)

_MAX_BODY_BYTES = 65536

# Janus signaling commands allowed through the proxy
_ALLOWED_JANUS_TYPES = frozenset({
    "create", "attach", "message", "trickle", "destroy",
    "detach", "keepalive", "claim", "info",
})

# Janus admin commands that MUST be blocked
_BLOCKED_JANUS_TYPES = frozenset({
    "add_token", "remove_token", "list_tokens",
    "accept_new_sessions", "set_session_timeout",
})

_proxy = AsyncHttpProxy(
    name="Janus",
    timeout=httpx.Timeout(connect=5.0, read=90.0, write=30.0, pool=60.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    extra_headers={"Connection": "keep-alive"},
)

start_client = _proxy.start
stop_client = _proxy.stop


async def forward_request(request: Request, subpath: str = "") -> Response:
    settings = get_settings()
    url = f"{settings.janus_http_base.rstrip('/')}/janus"
    if subpath:
        url = f"{url}/{subpath}"

    if request.method in ("POST", "PUT", "DELETE"):
        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body too large"},
            )

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None

        if isinstance(payload, dict) and "janus" in payload:
            janus_type = payload["janus"]
            if janus_type in _BLOCKED_JANUS_TYPES:
                log.warning("Blocked admin command: %s", janus_type)
                return JSONResponse(
                    status_code=403,
                    content={"error": f"Admin command '{janus_type}' is not allowed"},
                )
            if janus_type not in _ALLOWED_JANUS_TYPES:
                log.warning("Unknown Janus command: %s", janus_type)
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Unknown Janus command '{janus_type}'"},
                )

    return await _proxy.forward(request, url)
