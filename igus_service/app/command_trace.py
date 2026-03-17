from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request

from app.events import EventType


def publish_command_trace_event(
    request: Request,
    *,
    command_id: str,
    op_id: str | None,
    operation: str,
    result: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
    log_prefix: str = "command",
) -> None:
    request_id = getattr(request.state, "request_id", None)

    trace_snapshot: dict[str, Any] = {
        "ts": int(time.time() * 1000),
        "operation": operation,
        "request_id": request_id,
        "command_id": command_id,
        "op_id": op_id,
    }

    log = logger or logging.getLogger(__name__)

    try:
        request.app.state.latest_command_trace = trace_snapshot
    except Exception:
        log.debug("Failed to update latest command trace snapshot", exc_info=True)

    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return

    payload: dict[str, Any] = {
        "operation": operation,
        "command_id": command_id,
        "op_id": op_id,
        "request_id": request_id,
    }
    if result is not None:
        payload["result"] = result

    try:
        event_bus.publish(EventType.COMMAND, payload)
    except Exception:
        log.exception(
            "Failed to publish %s event operation=%s command_id=%s",
            log_prefix,
            operation,
            command_id,
        )
