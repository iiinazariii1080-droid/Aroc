import logging

from fastapi import APIRouter, Depends, Request

from app.auth import require_api_key

from app.application.commands import FaultResetCommand, MotionProfile, MoveCommand, ReferenceCommand
from app.application.drive_service import DriveService, ServiceError
from app.application.use_cases import DriveUseCases
from app.command_executor import run_command
from app.config import get_settings
from app.service_error_http import raise_service_error_http
from app.types import ActionResponse, MotionResponse, MoveParams, PositionResponse, StatusResponse

router = APIRouter(tags=["AE.01 (Igus)"])

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NOTE: LEGACY_ENDPOINTS is derived at the bottom of this module (after all
# routes are registered) so middleware can import it without a hardcoded copy.
# ---------------------------------------------------------------------------


def _use_cases(request: Request) -> DriveUseCases:
    return DriveUseCases(request.app.state)


@router.post("/move", response_model=ActionResponse, dependencies=[Depends(require_api_key)])
async def move_lift(params: MoveParams, request: Request):
    s = get_settings()
    velocity = int(params.velocity_percent / 100.0 * s.legacy_max_velocity)
    accel = int(params.acceleration_percent / 100.0 * s.legacy_max_acceleration)
    # Known limitation: the legacy /move endpoint has no separate
    # deceleration parameter, so deceleration is set equal to acceleration.
    # Use POST /drive/move_to_position for independent decel control.
    cmd = MoveCommand(
        target_position=int(params.position),
        relative=False,
        profile=MotionProfile(
            velocity=velocity,
            acceleration=accel,
            deceleration=accel,
        ),
        timeout_ms=30000,
    )

    command_id, data = await run_command(
        request,
        operation="move_to_position",
        invoke=lambda op_id: _use_cases(request).move_to_position(cmd, op_id=op_id),
        log_prefix="legacy command",
        logger=_LOGGER,
    )

    return ActionResponse(
        success=True,
        error="aborted by stop" if data.get("aborted") else None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )


@router.post("/reference", response_model=ActionResponse, dependencies=[Depends(require_api_key)])
async def reference(request: Request):
    command_id, data = await run_command(
        request,
        operation="reference",
        invoke=lambda op_id: _use_cases(request).reference(ReferenceCommand(timeout_ms=60000), op_id=op_id),
        log_prefix="legacy command",
        logger=_LOGGER,
    )

    return ActionResponse(
        success=True,
        error="aborted by stop" if data.get("aborted") else None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )


@router.post("/fault_reset", response_model=ActionResponse, dependencies=[Depends(require_api_key)])
async def fault_reset(request: Request):
    command_id, _ = await run_command(
        request,
        operation="fault_reset",
        invoke=lambda op_id: _use_cases(request).fault_reset(
            FaultResetCommand(auto_enable=True),
            op_id=op_id,
        ),
        log_prefix="legacy command",
        logger=_LOGGER,
    )

    return ActionResponse(
        success=True,
        error=None,
        request_id=getattr(request.state, "request_id", None),
        command_id=command_id,
    )

@router.get("/position", response_model=PositionResponse, dependencies=[Depends(require_api_key)])
async def get_lift_position(request: Request):
    try:
        drive = DriveService(request.app.state).get_drive(require_connected=True)
        position = await drive.get_position()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="position")
    return PositionResponse(position=float(position))

@router.get("/is_motion", response_model=MotionResponse, dependencies=[Depends(require_api_key)])
async def get_lift_motion(request: Request):
    is_moving = await _use_cases(request).get_is_moving()
    return MotionResponse(is_moving=is_moving)

@router.get("/status", response_model=StatusResponse, dependencies=[Depends(require_api_key)])
async def get_lift_status(request: Request):
    try:
        # get_drive_status() reads is_moving and is_homed atomically (Fix 16: TOCTOU).
        drive_status = await _use_cases(request).get_drive_status()
    except ServiceError as exc:
        raise_service_error_http(exc, request=request, operation="status")
    status_bits = drive_status.status_bits or {}
    has_error = bool(status_bits.get("fault", False))
    operation_enabled = bool(drive_status.enabled)
    position = 0.0 if drive_status.position is None else float(drive_status.position)
    last_error = getattr(request.app.state, "drive_last_error", None)
    return StatusResponse(
        status_word=int(drive_status.statusword),
        homed=drive_status.is_homed,
        is_moving=drive_status.is_moving,
        error=has_error,
        connected=drive_status.connected,
        position=position,
        enabled=operation_enabled,
        last_error=str(last_error) if last_error is not None else None,
    )


# ---------------------------------------------------------------------------
# Auto-derived set of legacy endpoint paths for use by middleware.
# Built after all routes are registered so it stays in sync automatically.
# ---------------------------------------------------------------------------
LEGACY_ENDPOINTS: frozenset[str] = frozenset(
    route.path  # type: ignore[union-attr]
    for route in router.routes
    if hasattr(route, "path")
)
