"""Shared command execution pipeline for route handlers.

Both legacy (routes.py) and v1 (api_routes.py) routes need the same pipeline:
  1. Generate command_id / op_id
  2. Invoke the use-case coroutine (passing op_id)
  3. Handle ServiceError → HTTPException via raise_service_error_http
  4. Publish the command trace event
  5. Return (command_id, data) for the caller to wrap in its own response model

Having a single implementation eliminates the copy-paste between the two route
modules and ensures that tracing, error handling, and ID generation stay in sync.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request

from app.application.drive_service import ServiceError
from app.command_trace import publish_command_trace_event
from app.service_error_http import raise_service_error_http

_LOGGER = logging.getLogger(__name__)


async def run_command(
    request: Request,
    *,
    operation: str,
    invoke: Callable[[str], Awaitable[dict[str, Any]]],
    log_prefix: str = "command",
    logger: logging.Logger | None = None,
) -> tuple[str, dict[str, Any]]:
    """Execute a drive command through the standard pipeline.

    Args:
        request:    The FastAPI request (used for request_id propagation and
                    app state access inside publish_command_trace_event).
        operation:  Human-readable operation name logged and stored in the trace.
        invoke:     Coroutine factory; called with ``op_id`` and must return a
                    plain dict that becomes the response payload.
        log_prefix: Prefix for the trace log line (e.g. "command", "legacy command").
        logger:     Logger to use; defaults to this module's logger.

    Returns:
        ``(command_id, data)`` — the caller wraps these in its response model.

    Raises:
        HTTPException: translated from ServiceError via raise_service_error_http.
    """
    command_id = uuid.uuid4().hex
    op_id = uuid.uuid4().hex[:8]
    log = logger or _LOGGER

    try:
        data = await invoke(op_id)
    except ServiceError as exc:
        publish_command_trace_event(
            request,
            command_id=command_id,
            op_id=op_id,
            operation=operation,
            result={"error": exc.code, "message": exc.message},
            logger=log,
            log_prefix=log_prefix,
        )
        raise_service_error_http(exc, request=request, operation=operation)

    publish_command_trace_event(
        request,
        command_id=command_id,
        op_id=op_id,
        operation=operation,
        result=data,
        logger=log,
        log_prefix=log_prefix,
    )
    return command_id, data
