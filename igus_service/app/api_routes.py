"""API v1 routes for drive control."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api_models import (
    ApiEnvelope,
    DriveStatus,
    FaultResetRequest,
    JogMoveRequest,
    Meta,
    MoveToPositionRequest,
    ReferenceRequest,
    StopRequest,
)
from app.application.drive_service import DriveService, ServiceError
from app.application.use_cases import DriveUseCases
from app.command_trace import publish_command_trace_event
from app.events import EventBus
from app.service_error_http import raise_service_error_http
from app.version import SERVER_VERSION

router = APIRouter(tags=["Drive API v1"])


def _drive_service(request: Request) -> DriveService:
    return DriveService(request.app.state)


def _use_cases(request: Request) -> DriveUseCases:
    return DriveUseCases(request.app.state)


def _build_meta(request: Request, *, command_id: str | None = None) -> Meta:
    return Meta(
        ts=int(time.time() * 1000),
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
        server_version=SERVER_VERSION,
    )


async def _execute_command(
    request: Request,
    *,
    operation: str,
    invoke: Callable[[str], Awaitable[dict]],
) -> ApiEnvelope[dict]:
    command_id = uuid.uuid4().hex
    op_id = uuid.uuid4().hex[:8]
    try:
        data = await invoke(op_id)
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation=operation)
        raise RuntimeError("Unreachable") from exc

    await publish_command_trace_event(
        request,
        command_id=command_id,
        op_id=op_id,
        operation=operation,
        result=data,
        logger=logging.getLogger(__name__),
        log_prefix="command",
    )

    return ApiEnvelope(
        ok=True,
        data=data,
        error=None,
        meta=_build_meta(request, command_id=command_id),
    )


@router.get("/drive/status", response_model=ApiEnvelope[DriveStatus])
async def get_drive_status(request: Request):
    """Get drive status from cache."""
    try:
        drive_status = await _use_cases(request).get_drive_status()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="status")
    
    # Build response envelope
    meta = _build_meta(request)
    
    return ApiEnvelope(
        ok=True,
        data=drive_status,
        error=None,
        meta=meta,
    )


@router.get("/drive/telemetry", response_model=ApiEnvelope[dict])
async def get_drive_telemetry(request: Request):
    """Get drive telemetry data."""
    try:
        telemetry_data = await _use_cases(request).get_drive_telemetry()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="telemetry")
    
    meta = _build_meta(request)
    
    return ApiEnvelope(
        ok=True,
        data=telemetry_data,
        error=None,
        meta=meta,
    )


def get_event_bus(request: Request) -> EventBus:
    """Get event bus from app state."""
    try:
        bus: EventBus = _drive_service(request).get_event_bus()
        return bus
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="events")
    raise RuntimeError("Unreachable")


@router.get("/drive/trace/latest", response_model=ApiEnvelope[dict])
async def get_latest_trace(request: Request):
    """Get latest command trace IDs for diagnostics correlation."""
    trace = getattr(request.app.state, "latest_command_trace", None)
    meta = _build_meta(request)
    data = {
        "has_trace": bool(trace is not None),
        "trace": trace,
    }
    return ApiEnvelope(
        ok=True,
        data=data,
        error=None,
        meta=meta,
    )


@router.post("/drive/move_to_position", response_model=ApiEnvelope[dict])
async def move_to_position(request: Request, req: MoveToPositionRequest):
    """Move to target position."""
    return await _execute_command(
        request,
        operation="move_to_position",
        invoke=lambda op_id: _use_cases(request).move_to_position(req, op_id=op_id),
    )


@router.post("/drive/jog_start", response_model=ApiEnvelope[dict])
async def jog_start(request: Request, req: JogMoveRequest):
    """Start jog movement."""
    return await _execute_command(
        request,
        operation="jog_start",
        invoke=lambda op_id: _use_cases(request).jog_start(req, op_id=op_id),
    )


@router.post("/drive/jog_update", response_model=ApiEnvelope[dict])
async def jog_update(request: Request, req: JogMoveRequest):
    """Update jog velocity and refresh TTL keepalive."""
    return await _execute_command(
        request,
        operation="jog_update",
        invoke=lambda op_id: _use_cases(request).jog_update(req, op_id=op_id),
    )


@router.post("/drive/jog_stop", response_model=ApiEnvelope[dict])
async def jog_stop_endpoint(request: Request):
    """Stop jog movement."""
    return await _execute_command(
        request,
        operation="jog_stop",
        invoke=lambda op_id: _use_cases(request).jog_stop(op_id=op_id),
    )


@router.post("/drive/jog_move", response_model=ApiEnvelope[dict])
async def jog_move(request: Request, req: JogMoveRequest):
    """Start jog movement (alias for jog_start)."""
    return await jog_start(request, req)


@router.post("/drive/stop", response_model=ApiEnvelope[dict])
async def stop_drive(request: Request, req: StopRequest):
    """Stop drive movement."""
    return await _execute_command(
        request,
        operation="stop",
        invoke=lambda op_id: _use_cases(request).stop(req, op_id=op_id),
    )


@router.post("/drive/reference", response_model=ApiEnvelope[dict])
async def reference_drive(request: Request, req: ReferenceRequest):
    """Perform reference/homing operation."""
    return await _execute_command(
        request,
        operation="reference",
        invoke=lambda op_id: _use_cases(request).reference(req, op_id=op_id),
    )


@router.post("/drive/fault_reset", response_model=ApiEnvelope[dict])
async def fault_reset_drive(request: Request, req: FaultResetRequest):
    """Reset fault and recover."""
    return await _execute_command(
        request,
        operation="fault_reset",
        invoke=lambda op_id: _use_cases(request).fault_reset(req, op_id=op_id),
    )


@router.get("/drive/events")
async def drive_events(request: Request):
    """Server-Sent Events stream for drive events."""
    event_bus = get_event_bus(request)
    
    async def event_generator():
        """Generate SSE events."""
        sub_queue = event_bus.subscribe()
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for event with timeout
                    event = await asyncio.wait_for(sub_queue.get(), timeout=1.0)
                    
                    # Format as SSE (Server-Sent Events format)
                    event_data = json.dumps({
                        "seq": event.seq,
                        "ts": event.ts,
                        "type": event.type.value,
                        "payload": event.payload,
                    })
                    yield f"event: {event.type.value}\ndata: {event_data}\n\n"
                except TimeoutError:
                    # Send keepalive
                    ping_data = json.dumps({"ts": int(time.time() * 1000)})
                    yield f"event: ping\ndata: {ping_data}\n\n"
                except Exception as e:
                    logging.getLogger(__name__).exception("Error in event stream: %s", e)
                    break
        finally:
            event_bus.unsubscribe(sub_queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
