"""
REST API endpoints for AE.HUB UI.
"""
from typing import List, Dict, Any, Optional, AsyncIterator
import asyncio
import json
import re
import time
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from app.dependencies import (
    require_command_auth,
    InjectedCommandHandler,
    InjectedEventBus,
    InjectedEventStream,
    InjectedStateStore,
    SymovoClient,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid
import logging

_LOGGER = logging.getLogger(__name__)

_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
from app.config import settings, teleop_config
from domain.models import NavigationCommand
from domain.state_machine import NavigationStateMachine


router = APIRouter(
    prefix="/api/v1",
    tags=["AE.HUB UI"],
)


class RobotInfo(BaseModel):
    """Robot information."""
    id: str = Field(..., description="Robot ID")
    name: Optional[str] = Field(default=None, description="Robot name")


class RobotListResponse(BaseModel):
    """Response for robots list."""
    robots: List[RobotInfo]

class NavigateToRequest(BaseModel):
    """Request to send driveToPosition command."""
    target_id: str = Field(default="", description="Target label for logs")
    command_id: Optional[str] = Field(default=None, description="Optional UUIDv4; generated if missing")
    timestamp: Optional[str] = Field(default=None, description="Optional ISO8601 timestamp; generated if missing")
    x: Optional[float] = Field(default=None, description="X coordinate (meters)")
    y: Optional[float] = Field(default=None, description="Y coordinate (meters)")
    theta: float = Field(default=0.0, description="Heading (radians)")
    map_id: int = Field(default=0, description="Map ID")
    station_id: Optional[int] = Field(default=None, description="Symovo station ID")
    max_speed_m_s: Optional[float] = Field(default=None, description="Max speed (m/s)")

class CancelRequest(BaseModel):
    """Request to send cancel command."""
    command_id: str = Field(..., min_length=1, description="Command id to cancel")
    timestamp: Optional[str] = Field(default=None, description="Optional ISO8601 timestamp; generated if missing")


class CommandSendResponse(BaseModel):
    status: str = Field(default="ok")
    topic: str
    payload: Dict[str, Any]


class MoveSpeedRequest(BaseModel):
    """Teleop speed command (joystick/keyboard)."""
    speed: Optional[float] = Field(default=None, ge=-2.0, le=2.0, description="Linear speed, m/s")
    angular_speed: Optional[float] = Field(default=None, ge=-3.0, le=3.0, description="Angular speed, rad/s")
    linear_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Linear direction: -1/0/1")
    angular_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Angular direction: -1/0/1")
    duration: Optional[float] = Field(default=None, ge=0.01, le=10.0, description="Duration, s")


class TeleopConfigResponse(BaseModel):
    """Current teleop default parameters."""
    duration: float = Field(..., ge=0.05, le=2.0, description="Segment duration, s")
    linear_m_s: float = Field(..., ge=0.01, le=1.0, description="Default linear speed, m/s")
    angular_rad_s: float = Field(..., ge=0.01, le=2.0, description="Default angular speed, rad/s")


class TeleopConfigUpdate(BaseModel):
    """Partial update of teleop default parameters."""
    duration: Optional[float] = Field(default=None, ge=0.05, le=2.0)
    linear_m_s: Optional[float] = Field(default=None, ge=0.01, le=1.0)
    angular_rad_s: Optional[float] = Field(default=None, ge=0.01, le=2.0)


def _teleop_config_payload() -> Dict[str, float]:
    return teleop_config.snapshot()


@router.get(
    "/robots/{robot_id}/teleop/config",
    response_model=TeleopConfigResponse,
    summary="Get current teleop default parameters",
)
async def get_teleop_config(
    robot_id: str = Path(..., description="Robot ID"),
) -> TeleopConfigResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    return TeleopConfigResponse(**_teleop_config_payload())


@router.put(
    "/robots/{robot_id}/teleop/config",
    response_model=TeleopConfigResponse,
    summary="Update teleop default parameters",
)
async def put_teleop_config(
    req: TeleopConfigUpdate,
    robot_id: str = Path(..., description="Robot ID"),
) -> TeleopConfigResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    if req.duration is not None:
        teleop_config.duration = float(req.duration)
    if req.linear_m_s is not None:
        teleop_config.linear_speed = float(req.linear_m_s)
    if req.angular_rad_s is not None:
        teleop_config.angular_speed = float(req.angular_rad_s)
    return TeleopConfigResponse(**_teleop_config_payload())


@router.get(
    "/robots",
    response_model=RobotListResponse,
    summary="List robots",
    description="Returns list of available robots for AE.HUB UI."
)
async def get_robots() -> RobotListResponse:
    """Get list of robots."""
    robots = [
        RobotInfo(
            id=settings.robot_id,
            name=f"Robot {settings.robot_id}"
        )
    ]
    return RobotListResponse(robots=robots)


@router.get(
    "/robots/{robot_id}/events",
    summary="SSE stream of ack/state/result events",
    description="Server-Sent Events stream for AE.HUB navigation lifecycle (ack/state/result).",
)
async def stream_events(
    request: Request,
    event_stream: InjectedEventStream,
    _event_bus: InjectedEventBus,
    robot_id: str = Path(..., description="Robot ID"),
):
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    async def gen() -> AsyncIterator[bytes]:
        if not await event_stream.sse_try_increment():
            yield b"event: error\ndata: {\"detail\": \"Too many SSE connections\"}\n\n"
            return
        q = None
        try:
            q = await _event_bus.subscribe()
            _MAX_SSE_LIFETIME_S = 3600
            _sse_start = time.time()
            yield b": connected\n\n"
            while True:
                if await request.is_disconnected() or (time.time() - _sse_start) > _MAX_SSE_LIFETIME_S:
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    payload = json.dumps(event.model_dump(), ensure_ascii=False)
                    msg = f"event: {event.type}\ndata: {payload}\n\n"
                    yield msg.encode("utf-8")
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
        finally:
            if q is not None:
                await _event_bus.unsubscribe(q)
            await event_stream.sse_decrement()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/robots/{robot_id}/events/poll",
    summary="Poll queued events (proxy-safe alternative to SSE)",
)
async def poll_events(
    event_stream: InjectedEventStream,
    bus: InjectedEventBus,
    robot_id: str = Path(..., description="Robot ID"),
    client_id: str = Query(default="default", max_length=64, description="Client ID for poll queue isolation"),
):
    """Return all events queued since the last poll for this client_id."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=422, detail="client_id must be 1-64 alphanumeric/dash/underscore chars")
    try:
        q = await event_stream.get_poll_queue(client_id)
    except RuntimeError:
        raise HTTPException(status_code=429, detail="Too many polling clients; try again later")
    events = await bus.drain(q)
    return {"events": [e.model_dump() for e in events]}


@router.get(
    "/robots/{robot_id}/status/navigation",
    summary="Get current navigation status",
    description="Returns current navigation status (idle/navigating/arrived/error)."
)
async def get_navigation_status(
    store: InjectedStateStore,
    robot_id: str = Path(..., description="Robot ID")
) -> Dict[str, Any]:
    """Get current navigation status."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    last_status = await store.get_last_navigation_status()
    if last_status:
        return last_status.model_dump()

    return {
        "status": "idle",
        "goal_id": None,
        "progress_percent": 0,
        "eta_seconds": None,
        "error_reason": None
    }


@router.get(
    "/robots/{robot_id}/status/position",
    summary="Get current position",
    description="Returns current robot position (x, y, theta)."
)
async def get_position_status(
    store: InjectedStateStore,
    robot_id: str = Path(..., description="Robot ID")
) -> Dict[str, Any]:
    """Get current position status."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    last_position = await store.get_last_position_status()
    if last_position:
        return last_position.model_dump()

    return {
        "x": 0.0,
        "y": 0.0,
        "theta": 0.0,
        "frame_id": "map"
    }


@router.post(
    "/robots/{robot_id}/commands/driveToPosition",
    response_model=CommandSendResponse,
    summary="Send driveToPosition command (AGV base only)",
)
async def send_drive_to_position(
    req: NavigateToRequest,
    handler: InjectedCommandHandler,
    robot_id: str = Path(..., description="Robot ID"),
    _auth: None = Depends(require_command_auth),
) -> CommandSendResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    command_id = req.command_id or str(uuid.uuid4())
    ts = req.timestamp or datetime.now(timezone.utc).isoformat()

    payload = {
        "command_id": command_id,
        "timestamp": ts,
        "target_id": req.target_id,
        "x": req.x,
        "y": req.y,
        "theta": req.theta,
        "map_id": req.map_id,
        "station_id": req.station_id,
        "max_speed_m_s": req.max_speed_m_s,
    }
    try:
        cmd = NavigationCommand(**payload)
        nav_status = await handler.handle_drive_to_position(cmd)
        res = {**payload, "navigation_status": nav_status.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": {"type": "CommandDeliveryFailed", "msg": str(e)}})

    return CommandSendResponse(topic="http", payload=res)


@router.post(
    "/robots/{robot_id}/commands/cancel",
    response_model=CommandSendResponse,
    summary="Send cancel command",
)
async def send_cancel(
    req: CancelRequest,
    handler: InjectedCommandHandler,
    robot_id: str = Path(..., description="Robot ID"),
    _auth: None = Depends(require_command_auth),
) -> CommandSendResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    ts = req.timestamp or datetime.now(timezone.utc).isoformat()

    try:
        nav_status = await handler.handle_cancel(req.command_id)
        res = {"command_id": req.command_id, "timestamp": ts, "navigation_status": nav_status.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": {"type": "CommandDeliveryFailed", "msg": str(e)}})

    return CommandSendResponse(topic="http", payload=res)


@router.api_route(
    "/robots/{robot_id}/move/speed",
    methods=["PUT", "POST"],
    summary="Teleop: speed command (joystick/keyboard)",
    description="Proxies to Symovo PUT /v0/agv/{id}/move/speed.",
)
async def move_speed(
    req: MoveSpeedRequest,
    symovo: SymovoClient,
    store: InjectedStateStore,
    robot_id: str = Path(..., description="Robot ID"),
    _auth: None = Depends(require_command_auth),
) -> Dict[str, Any]:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    # C2: reject teleop while a coordinated transport is active
    active = await store.get_all_active_commands()
    for _, t in active.items():
        if not NavigationStateMachine.is_terminal_state(t.state):
            raise HTTPException(
                status_code=409,
                detail={"error": {"type": "TransportActive",
                                  "msg": "Navigation in progress"}},
            )
    try:
        linear_dir = 0 if req.linear_dir is None else int(max(-1, min(1, req.linear_dir)))
        angular_dir = 0 if req.angular_dir is None else int(max(-1, min(1, req.angular_dir)))
        speed = req.speed if req.speed is not None else teleop_config.linear_speed * float(linear_dir)
        angular_speed = req.angular_speed if req.angular_speed is not None else teleop_config.angular_speed * float(angular_dir)
        duration = req.duration if req.duration is not None else teleop_config.duration
        result = await symovo.move_speed(
            speed=speed,
            angular_speed=angular_speed,
            duration=duration,
        )
        return result if isinstance(result, dict) else {"status": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"error": {"type": "MoveSpeedFailed", "msg": str(e)}},
        ) from e
