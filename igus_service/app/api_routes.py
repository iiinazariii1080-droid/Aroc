"""API v1 routes for drive control."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth import require_api_key

from app.api_models import (
    ApiEnvelope,
    CiA402State,
    CommandTrace,
    DriveOnlineState,
    DriveStatus,
    FaultDetails,
    FaultInfo,
    FaultResetRequest,
    FaultResetResult,
    JogMoveRequest,
    JogResult,
    JogStopResult,
    Meta,
    MoveToPositionRequest,
    MoveToPositionResult,
    OperationMode,
    ReferenceRequest,
    ReferenceResult,
    StopRequest,
    StopResult,
    TelemetryResponse,
    TraceResponse,
)
from app.application.commands import (
    FaultResetCommand,
    JogCommand,
    MotionProfile,
    MoveCommand,
    ReferenceCommand,
    StopCommand,
)
from app.application.drive_service import DriveService, ServiceError
from app.application.results import DriveStatusResult
from app.application.use_cases import DriveUseCases
from app.command_executor import run_command
from app.events import DROPPED, DriveEvent, EventBus, EventType
from app.service_error_http import raise_service_error_http
from app.version import SERVER_VERSION

router = APIRouter(tags=["Drive API v1"])


def _status_result_to_api(result: DriveStatusResult) -> DriveStatus:
    """Map application-layer DriveStatusResult to the API presentation model."""
    fault_details = None
    if result.fault.details is not None:
        fault_details = FaultDetails(
            error_code=result.fault.details.error_code,
            error_register=result.fault.details.error_register,
            history=result.fault.details.history,
        )
    return DriveStatus(
        online=DriveOnlineState(result.online),
        last_poll_ts=result.last_poll_ts,
        poll_period_ms=result.poll_period_ms,
        cia402_state=CiA402State(result.cia402_state),
        mode_display=OperationMode(result.mode_display) if result.mode_display else None,
        statusword=result.statusword,
        status_bits=result.status_bits,
        remote=result.remote,
        enabled=result.enabled,
        position=result.position,
        velocity=result.velocity,
        is_moving=result.is_moving,
        is_homed=result.is_homed,
        fault=FaultInfo(active=result.fault.active, details=fault_details),
    )


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
) -> ApiEnvelope[Any]:
    command_id, data = await run_command(
        request,
        operation=operation,
        invoke=invoke,
        log_prefix="command",
        logger=logging.getLogger(__name__),
    )
    return ApiEnvelope(
        ok=True,
        data=data,
        error=None,
        meta=_build_meta(request, command_id=command_id),
    )


def get_event_bus(request: Request) -> EventBus:
    """Get event bus from app state.

    Always either returns an EventBus or raises HTTPException via
    raise_service_error_http — never returns None implicitly.
    """
    try:
        return _drive_service(request).get_event_bus()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="events")
        raise RuntimeError("raise_service_error_http must raise")  # unreachable; satisfies type checker


@router.get("/drive/status", response_model=ApiEnvelope[DriveStatus], dependencies=[Depends(require_api_key)])
async def get_drive_status(request: Request):
    """Get drive status (CiA 402 state, position, velocity, fault).

    Returns cached telemetry when available; falls back to a live read.
    Returns 503 DRIVE_NOT_INITIALIZED if the driver was not started,
    503 DRIVE_OFFLINE if the drive is disconnected,
    503 STATUS_READ_FAILED if the live read fails.
    """
    try:
        result = await _use_cases(request).get_drive_status()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="status")
        raise AssertionError("unreachable")  # raise_service_error_http is NoReturn

    return ApiEnvelope(
        ok=True,
        data=_status_result_to_api(result),
        error=None,
        meta=_build_meta(request),
    )


@router.get("/drive/telemetry", response_model=ApiEnvelope[TelemetryResponse], dependencies=[Depends(require_api_key)])
async def get_drive_telemetry(request: Request):
    """Get a lightweight telemetry snapshot (position, velocity, statusword).

    Returns 503 TELEMETRY_READ_FAILED if the drive cannot be read.
    """
    try:
        telemetry_data = await _use_cases(request).get_drive_telemetry()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="telemetry")
        raise AssertionError("unreachable")  # raise_service_error_http is NoReturn

    return ApiEnvelope(
        ok=True,
        data=telemetry_data,
        error=None,
        meta=_build_meta(request),
    )


@router.get("/drive/trace/latest", response_model=ApiEnvelope[TraceResponse], dependencies=[Depends(require_api_key)])
async def get_latest_trace(request: Request):
    """Get the latest command trace for diagnostics correlation.

    Returns the most recent command_id / op_id pair so that log lines
    can be correlated across the app and driver layers.
    """
    trace_raw = getattr(request.app.state, "latest_command_trace", None)
    meta = _build_meta(request)
    data: TraceResponse
    if trace_raw is None:
        data = TraceResponse(has_trace=False, trace=None)
    else:
        data = TraceResponse(
            has_trace=True,
            trace=CommandTrace(
                ts=trace_raw.get("ts", 0),
                operation=trace_raw.get("operation", ""),
                request_id=trace_raw.get("request_id"),
                command_id=trace_raw.get("command_id"),
                op_id=trace_raw.get("op_id"),
            ),
        )
    return ApiEnvelope(ok=True, data=data, error=None, meta=meta)


@router.post("/drive/move_to_position", response_model=ApiEnvelope[MoveToPositionResult], dependencies=[Depends(require_api_key)])
async def move_to_position(request: Request, req: MoveToPositionRequest):
    """Move to absolute or relative target position using Profile Position mode.

    Returns 409 DRIVE_IN_FAULT if a fault is active (call fault_reset first),
    409 MOTOR_BUSY if another motion command is in progress,
    503 DRIVE_OFFLINE if the drive is disconnected,
    504 TIMEOUT if the target position is not reached within timeout_ms.
    """
    use_cases = _use_cases(request)
    cmd = MoveCommand(
        target_position=int(req.target_position),
        relative=req.relative,
        profile=MotionProfile(
            velocity=int(req.profile.velocity),
            acceleration=int(req.profile.acceleration),
            deceleration=int(req.profile.deceleration),
        ),
        timeout_ms=req.timeout_ms,
    )
    return await _execute_command(
        request,
        operation="move_to_position",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.move_to_position(_cmd, op_id=op_id),
    )


@router.post("/drive/jog_start", response_model=ApiEnvelope[JogResult], dependencies=[Depends(require_api_key)])
async def jog_start(request: Request, req: JogMoveRequest):
    """Start continuous jog movement in the specified direction.

    The jog watchdog expires after ttl_ms milliseconds; send jog_update
    periodically to keep the motor moving. Returns 409 MOTOR_BUSY if
    another motion command holds the motor lock.
    """
    use_cases = _use_cases(request)
    cmd = JogCommand(direction=req.direction, speed=req.speed, ttl_ms=req.ttl_ms)
    return await _execute_command(
        request,
        operation="jog_start",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.jog_start(_cmd, op_id=op_id),
    )


@router.post("/drive/jog_update", response_model=ApiEnvelope[JogResult], dependencies=[Depends(require_api_key)])
async def jog_update(request: Request, req: JogMoveRequest):
    """Refresh jog watchdog TTL and optionally update velocity.

    Must be called before the TTL set in jog_start expires, otherwise
    the drive will stop automatically. Returns 503 DRIVE_OFFLINE if
    the drive is disconnected.
    """
    use_cases = _use_cases(request)
    cmd = JogCommand(direction=req.direction, speed=req.speed, ttl_ms=req.ttl_ms)
    return await _execute_command(
        request,
        operation="jog_update",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.jog_update(_cmd, op_id=op_id),
    )


@router.post("/drive/jog_stop", response_model=ApiEnvelope[JogStopResult], dependencies=[Depends(require_api_key)])
async def jog_stop_endpoint(request: Request):
    """Stop active jog movement.

    Safe to call even if no jog is active. Returns 503 DRIVE_OFFLINE
    if the drive is disconnected.
    """
    use_cases = _use_cases(request)
    return await _execute_command(
        request,
        operation="jog_stop",
        invoke=lambda op_id, _uc=use_cases: _uc.jog_stop(op_id=op_id),
    )


@router.post("/drive/stop", response_model=ApiEnvelope[StopResult], dependencies=[Depends(require_api_key)])
async def stop_drive(request: Request, req: StopRequest):
    """Stop drive movement using quick_stop or halt mode.

    quick_stop: drive decelerates using its programmed quick-stop ramp.
    halt: drive decelerates using its normal deceleration ramp.
    Returns 503 DRIVE_OFFLINE if the drive is disconnected.
    """
    use_cases = _use_cases(request)
    cmd = StopCommand(mode=req.mode)
    return await _execute_command(
        request,
        operation="stop",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.stop(_cmd, op_id=op_id),
    )


@router.post("/drive/reference", response_model=ApiEnvelope[ReferenceResult], dependencies=[Depends(require_api_key)])
async def reference_drive(request: Request, req: ReferenceRequest):
    """Perform homing (reference) operation.

    The drive moves to its reference point using the configured homing
    method. Returns 409 DRIVE_IN_FAULT if a fault is active,
    409 MOTOR_BUSY if another motion command is in progress,
    504 TIMEOUT if homing does not complete within timeout_ms.
    aborted=True in the response means a stop command interrupted homing.
    """
    use_cases = _use_cases(request)
    cmd = ReferenceCommand(timeout_ms=req.timeout_ms)
    return await _execute_command(
        request,
        operation="reference",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.reference(_cmd, op_id=op_id),
    )


@router.post("/drive/fault_reset", response_model=ApiEnvelope[FaultResetResult], dependencies=[Depends(require_api_key)])
async def fault_reset_drive(request: Request, req: FaultResetRequest):
    """Reset an active drive fault and optionally re-enable operation.

    Reads fault diagnostics before reset so they are preserved in the
    response even after the drive clears them. Returns 503 DRIVE_OFFLINE
    if the drive is disconnected.
    """
    use_cases = _use_cases(request)
    cmd = FaultResetCommand(auto_enable=req.auto_enable)
    return await _execute_command(
        request,
        operation="fault_reset",
        invoke=lambda op_id, _uc=use_cases, _cmd=cmd: _uc.fault_reset(_cmd, op_id=op_id),
    )


@router.get("/drive/events", dependencies=[Depends(require_api_key)])
async def drive_events(request: Request):
    """Server-Sent Events stream for real-time drive state notifications.

    Event types: status, state_change, fault, command.
    A keepalive ping event is sent every ~1 second when no other events occur.
    Slow consumers whose queue fills (>100 events) are automatically dropped.
    Late-joining clients receive the last 20 events for immediate state catch-up.
    """
    event_bus = get_event_bus(request)

    def _format_event(event) -> str:
        event_data = json.dumps({
            "seq": event.seq,
            "ts": event.ts,
            "type": event.type.value,
            "payload": event.payload,
        })
        return f"event: {event.type.value}\ndata: {event_data}\n\n"

    async def event_generator():
        # SSE retry field: instruct clients to reconnect after 1s on disconnect.
        yield "retry: 1000\n\n"

        # Replay recent events before subscribing so late-joiners get current state.
        for past_event in event_bus.get_recent_events(limit=20):
            yield _format_event(past_event)

        sub_queue = event_bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(sub_queue.get(), timeout=1.0)
                    if event is DROPPED:
                        # Subscriber was dropped (slow consumer); send error event
                        # with reason before closing so the client can act on it.
                        error_data = json.dumps({"reason": "slow_consumer"})
                        yield f"event: error\ndata: {error_data}\n\n"
                        logging.getLogger(__name__).warning(
                            "SSE subscriber dropped (queue full); closing stream."
                        )
                        break
                    if isinstance(event, DriveEvent) and event.type == EventType.SHUTDOWN:
                        yield _format_event(event)
                        break  # server is shutting down — close stream gracefully
                    yield _format_event(event)
                except TimeoutError:
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
