import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, status

from app.api_models import FaultResetRequest, MoveToPositionRequest, ProfileConfig, ReferenceRequest
from app.application.drive_service import ServiceError
from app.application.use_cases import DriveUseCases
from app.command_trace import publish_command_trace_event
from app.decorator import safe_getter
from app.http_errors import error_detail, is_drive_connected
from app.service_error_http import raise_service_error_http
from app.types import ActionResponse, MotionResponse, MoveParams, PositionResponse, StatusResponse

router = APIRouter(tags=["AE.01 (Igus)"])

# Default values for velocity/acceleration conversion (percent to absolute)
DEFAULT_MAX_VELOCITY = 10000  # drive units/s
DEFAULT_MAX_ACCELERATION = 5000  # drive units/s²

_LOGGER = logging.getLogger(__name__)


def _get_drive(request: Request, *, require_connected: bool = False):
    drive = getattr(request.app.state, "drive", None)
    if drive is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("DRIVE_NOT_INITIALIZED", "Driver not initialized"),
        )
    if require_connected and not is_drive_connected(drive):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("DRIVE_OFFLINE", "Driver is not connected"),
        )
    return drive


def _use_cases(request: Request) -> DriveUseCases:
    return DriveUseCases(request.app.state)


async def _execute_legacy_command(
    request: Request,
    *,
    operation: str,
    invoke: Callable[[str], Awaitable[dict]],
) -> tuple[str, dict]:
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
        logger=_LOGGER,
        log_prefix="legacy command",
    )
    return command_id, data


@router.post("/move", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def move_lift(params: MoveParams, request: Request):
    velocity = int(params.velocity_percent / 100.0 * DEFAULT_MAX_VELOCITY)
    accel = int(params.acceleration_percent / 100.0 * DEFAULT_MAX_ACCELERATION)
    req = MoveToPositionRequest(
        target_position=float(params.position),
        relative=False,
        profile=ProfileConfig(
            velocity=float(velocity),
            acceleration=float(accel),
            deceleration=float(accel),
        ),
        timeout_ms=30000,
    )

    command_id, data = await _execute_legacy_command(
        request,
        operation="move_to_position",
        invoke=lambda op_id: _use_cases(request).move_to_position(req, op_id=op_id),
    )

    return ActionResponse(
        success=True,
        error="aborted by stop" if data.get("aborted") else None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )


@router.post("/reference", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def reference(request: Request):
    command_id, data = await _execute_legacy_command(
        request,
        operation="reference",
        invoke=lambda op_id: _use_cases(request).reference(ReferenceRequest(timeout_ms=60000), op_id=op_id),
    )

    return ActionResponse(
        success=True,
        error="aborted by stop" if data.get("aborted") else None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )


@router.post("/fault_reset", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def fault_reset(request: Request):
    command_id, _ = await _execute_legacy_command(
        request,
        operation="fault_reset",
        invoke=lambda op_id: _use_cases(request).fault_reset(
            FaultResetRequest(after_reset={"auto_enable": True}, timeout_ms=15000),
            op_id=op_id,
        )
    )

    return ActionResponse(
        success=True,
        error=None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )

@router.get("/position", response_model=PositionResponse)
@safe_getter(PositionResponse)
async def get_lift_position(request: Request):
    drive = _get_drive(request)
    position = await drive.get_position()
    return PositionResponse(position=float(position))

@router.get("/is_motion", response_model=MotionResponse)
@safe_getter(MotionResponse)
async def get_lift_motion(request: Request):
    drive = _get_drive(request)
    is_moving = await drive.is_motion()
    return MotionResponse(is_moving=is_moving)

@router.get("/status", response_model=StatusResponse)
@safe_getter(StatusResponse)
async def get_lift_status(request: Request):
    drive = _get_drive(request)
    try:
        drive_status = await _use_cases(request).get_drive_status()
        status_bits = drive_status.status_bits or {}
        is_moving = await drive.is_motion()
        is_homed = await drive.is_homed()
        has_error = bool(status_bits.get("fault", False))
        operation_enabled = bool(drive_status.enabled)
        is_connected = bool(getattr(drive, "is_connected", False))
        position = 0.0 if drive_status.position is None else float(drive_status.position)

        # Clear last error on successful round-trip
        request.app.state.drive_last_error = None

        return StatusResponse(
            status_word=int(drive_status.statusword),
            homed=bool(is_homed),
            is_moving=bool(is_moving),
            error=has_error,
            connected=is_connected,
            position=position,
            enabled=operation_enabled,
            last_error=None,
        )
    except Exception as exc:
        # Never blow up the UI polling loop — report degraded status instead.
        msg = str(exc)
        request.app.state.drive_last_error = msg

        is_connected = bool(getattr(drive, "is_connected", False))
        return StatusResponse(
            status_word=0,
            homed=False,
            is_moving=False,
            error=True,
            connected=is_connected,
            position=0.0,
            enabled=False,
            last_error=msg,
        )


