"""
REST API endpoints for AE.HUB UI.
"""
from typing import List, Dict, Any, Optional, AsyncIterator
import asyncio
import json
import re
import time
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid
import logging

_logger = logging.getLogger(__name__)

# ---------- SSE connection limiter ----------
_SSE_MAX_CLIENTS = 100
_sse_active = 0
_sse_lock = asyncio.Lock()

_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
from app.config import settings, teleop_config
from services.event_bus import event_bus
from services.state_store import state_store
from domain.models import NavigationStatus, PositionStatus
from db.robot_positions import get_robot_positions_list
from services.mqtt_adapter import MqttUnavailableError
try:
    from aiomqtt.exceptions import MqttCodeError
except ImportError:
    class MqttCodeError(Exception):
        pass

router = APIRouter(
    prefix="/api/v1",
    tags=["AE.HUB UI"],
)


class PositionInfo(BaseModel):
    """Position information."""
    id: str = Field(..., description="Position ID (e.g., position_A)")
    name: Optional[str] = Field(default=None, description="Optional name stored in DB")
    label: str = Field(..., description="Human-readable label")
    description: Optional[str] = Field(default=None, description="Optional description")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Raw params object from DB (extra fields preserved)")


class RobotInfo(BaseModel):
    """Robot information."""
    id: str = Field(..., description="Robot ID")
    name: Optional[str] = Field(default=None, description="Robot name")


class RobotListResponse(BaseModel):
    """Response for robots list."""
    robots: List[RobotInfo]


class PositionListResponse(BaseModel):
    """Response for positions list."""
    positions: List[PositionInfo]

class DriveToPositionRequest(BaseModel):
    """Request to send driveToPosition command (AGV base only) via backend -> MQTT."""
    target_id: str = Field(..., min_length=1, description="Target position name (must match DB name)")
    command_id: Optional[str] = Field(default=None, description="Optional UUIDv4; generated if missing")
    timestamp: Optional[str] = Field(default=None, description="Optional ISO8601 timestamp; generated if missing")


class CancelRequest(BaseModel):
    """Request to send cancel command via backend -> MQTT."""
    command_id: str = Field(..., min_length=1, description="Command id to cancel")
    timestamp: Optional[str] = Field(default=None, description="Optional ISO8601 timestamp; generated if missing")


class CommandSendResponse(BaseModel):
    status: str = Field(default="ok")
    topic: str
    payload: Dict[str, Any]


class MoveSpeedRequest(BaseModel):
    """Тело запроса для телеуправления (джойстик/клавиатура). OpenAPI MoveSpeed."""
    speed: Optional[float] = Field(default=None, ge=-2.0, le=2.0, description="Линейная скорость, м/с (опционально; default из настроек)")
    angular_speed: Optional[float] = Field(default=None, ge=-3.0, le=3.0, description="Угловая скорость, рад/с (опционально; default из настроек)")
    linear_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Направление линейной скорости: -1/0/1 (опционально)")
    angular_dir: Optional[int] = Field(default=None, ge=-1, le=1, description="Направление угловой скорости: -1/0/1 (опционально)")
    duration: Optional[float] = Field(default=None, ge=0.01, le=10.0, description="Длительность, с (опционально; default из настроек)")


class TeleopConfigResponse(BaseModel):
    """Текущие default-параметры телеуправления, применяемые при direction-only командах."""
    duration: float = Field(..., ge=0.05, le=2.0, description="Длительность сегмента, с")
    linear_m_s: float = Field(..., ge=0.01, le=1.0, description="Линейная скорость по умолчанию, м/с")
    angular_rad_s: float = Field(..., ge=0.01, le=2.0, description="Угловая скорость по умолчанию, рад/с")


class TeleopConfigUpdate(BaseModel):
    """Частичное обновление default-параметров телеуправления."""
    duration: Optional[float] = Field(default=None, ge=0.05, le=2.0)
    linear_m_s: Optional[float] = Field(default=None, ge=0.01, le=1.0)
    angular_rad_s: Optional[float] = Field(default=None, ge=0.01, le=2.0)


def _teleop_config_payload() -> Dict[str, float]:
    return teleop_config.snapshot()


@router.get(
    "/robots/{robot_id}/teleop/config",
    response_model=TeleopConfigResponse,
    summary="Текущие default-параметры teleop",
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
    summary="Обновить default-параметры teleop",
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
    # For MVP, return single robot from config
    robots = [
        RobotInfo(
            id=settings.robot_id,
            name=f"Robot {settings.robot_id}"
        )
    ]
    return RobotListResponse(robots=robots)


@router.get(
    "/robots/{robot_id}/positions",
    response_model=PositionListResponse,
    summary="List positions",
    description="Returns saved positions from the database (default limit=5)."
)
async def get_positions(
    robot_id: str = Path(..., description="Robot ID"),
    limit: int = 5,
) -> PositionListResponse:
    """Get list of positions for a robot."""
    # Validate robot_id
    if robot_id != settings.robot_id:
        raise HTTPException(
            status_code=404,
            detail=f"Robot {robot_id} not found"
        )
    # Clamp limit to a sane range
    limit = max(1, min(limit, 100))

    # SQLite access is blocking; run in a thread.
    rows = await asyncio.to_thread(get_robot_positions_list)
    # Return at most `limit` positions
    rows = (rows or [])[:limit]

    positions: List[PositionInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        if not isinstance(pid, str) or not pid:
            continue
        name = row.get("name") if isinstance(row.get("name"), str) else None
        params = row.get("params") if isinstance(row.get("params"), dict) else None

        # Prefer human-friendly label:
        # - explicit DB name
        # - params.position_id (often exists in your payload)
        # - fallback to id
        label = pid
        if name:
            label = name
        elif isinstance(params, dict) and isinstance(params.get("position_id"), str) and params.get("position_id"):
            label = str(params.get("position_id"))

        # Optional description: allow storing it inside params (your example does this)
        desc = None
        if isinstance(params, dict) and isinstance(params.get("description"), str) and params.get("description"):
            desc = str(params.get("description"))
        positions.append(
            PositionInfo(
                id=pid,
                name=name,
                label=label,
                description=desc,
                params=params,
            )
        )

    return PositionListResponse(positions=positions)


@router.get(
    "/robots/{robot_id}/events",
    summary="SSE stream of ack/state/result events",
    description="Server-Sent Events stream for AE.HUB navigation lifecycle (ack/state/result).",
)
async def stream_events(
    request: Request,
    robot_id: str = Path(..., description="Robot ID"),
):
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    async def gen() -> AsyncIterator[bytes]:
        global _sse_active
        async with _sse_lock:
            if _sse_active >= _SSE_MAX_CLIENTS:
                yield b"event: error\ndata: {\"detail\": \"Too many SSE connections\"}\n\n"
                return
            _sse_active += 1
        _MAX_SSE_LIFETIME_S = 3600  # Force client reconnect after 1 hour
        _sse_start = time.time()
        q = await event_bus.subscribe()
        try:
            # Initial comment to open stream
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
                    # keep-alive ping
                    yield b": ping\n\n"
        finally:
            await event_bus.unsubscribe(q)
            async with _sse_lock:
                _sse_active -= 1

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # SSE best-practice headers (avoid buffering and caching)
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- Per-client poll queues (lightweight, auto-expire) ----------
import time as _time

_poll_queues: Dict[str, tuple] = {}  # client_id -> (queue, last_access_time)
_poll_lock = asyncio.Lock()
_POLL_TTL = 120  # seconds before idle queue is removed
_POLL_MAX_CLIENTS = 200  # hard cap on concurrent poll clients
_poll_cleanup_task: Optional[asyncio.Task] = None


async def _poll_cleanup_loop() -> None:
    """Periodically remove stale poll queues to prevent unbounded growth."""
    while True:
        try:
            await asyncio.sleep(60)
            async with _poll_lock:
                now = _time.monotonic()
                stale = [k for k, (_, t) in _poll_queues.items() if now - t > _POLL_TTL]
                for k in stale:
                    await event_bus.unsubscribe(_poll_queues.pop(k)[0])
                if stale:
                    import logging
                    logging.getLogger(__name__).debug("Cleaned %d stale poll queues", len(stale))
        except asyncio.CancelledError:
            break
        except Exception:
            pass  # best-effort


def cancel_poll_cleanup_task() -> None:
    """Cancel the poll cleanup background task (call during shutdown)."""
    global _poll_cleanup_task
    if _poll_cleanup_task is not None and not _poll_cleanup_task.done():
        _poll_cleanup_task.cancel()
    _poll_cleanup_task = None


async def _get_poll_queue(client_id: str) -> asyncio.Queue:
    """Return (or create) a per-client event queue for polling."""
    global _poll_cleanup_task
    async with _poll_lock:
        # Start background cleaner on first use
        if _poll_cleanup_task is None or _poll_cleanup_task.done():
            _poll_cleanup_task = asyncio.create_task(_poll_cleanup_loop())
        # Expire stale queues
        now = _time.monotonic()
        stale = [k for k, (_, t) in _poll_queues.items() if now - t > _POLL_TTL]
        for k in stale:
            await event_bus.unsubscribe(_poll_queues.pop(k)[0])
        if client_id in _poll_queues:
            q, _ = _poll_queues[client_id]
            _poll_queues[client_id] = (q, now)
            return q
        if len(_poll_queues) >= _POLL_MAX_CLIENTS:
            raise HTTPException(
                status_code=429,
                detail="Too many polling clients; try again later",
            )
        q = await event_bus.subscribe()
        _poll_queues[client_id] = (q, now)
        return q


@router.get(
    "/robots/{robot_id}/events/poll",
    summary="Poll queued events (proxy-safe alternative to SSE)",
)
async def poll_events(
    robot_id: str = Path(..., description="Robot ID"),
    client_id: str = Query(default="default", max_length=64, description="Client ID for poll queue isolation"),
):
    """Return all events queued since the last poll for this client_id."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=422, detail="client_id must be 1-64 alphanumeric/dash/underscore chars")
    q = await _get_poll_queue(client_id)
    events = await event_bus.drain(q)
    return {"events": [e.model_dump() for e in events]}


@router.get(
    "/robots/{robot_id}/status/navigation",
    summary="Get current navigation status",
    description="Returns current navigation status (idle/navigating/arrived/error)."
)
async def get_navigation_status(
    robot_id: str = Path(..., description="Robot ID")
) -> Dict[str, Any]:
    """Get current navigation status."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    
    last_status = await state_store.get_last_navigation_status()
    if last_status:
        return last_status.model_dump()
    
    # Default idle status
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
    robot_id: str = Path(..., description="Robot ID")
) -> Dict[str, Any]:
    """Get current position status."""
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    
    last_position = await state_store.get_last_position_status()
    if last_position:
        return last_position.model_dump()
    
    # Default position
    return {
        "x": 0.0,
        "y": 0.0,
        "theta": 0.0,
        "frame_id": "map"
    }


@router.post(
    "/robots/{robot_id}/commands/driveToPosition",
    response_model=CommandSendResponse,
    summary="Send driveToPosition command (AGV base only, publishes to MQTT)",
)
async def send_drive_to_position(
    req: DriveToPositionRequest,
    request: Request,
    robot_id: str = Path(..., description="Robot ID"),
) -> CommandSendResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    command_id = req.command_id or str(uuid.uuid4())
    ts = req.timestamp or datetime.now(timezone.utc).isoformat()

    facade = getattr(request.app.state, "navigation_facade", None)
    if facade is None:
        raise HTTPException(status_code=503, detail={"error": {"type": "NotReady", "msg": "Navigation service not ready"}})

    try:
        res = await facade.send_drive_to_position(target_id=req.target_id, command_id=command_id, timestamp=ts)
    except MqttUnavailableError as e:
        raise HTTPException(status_code=503, detail={"error": {"type": "MQTTUnavailable", "msg": str(e)}})
    except Exception as e:
        # Defensive: do not return 500 on transient transport errors.
        raise HTTPException(status_code=503, detail={"error": {"type": "CommandDeliveryFailed", "msg": str(e)}})

    return CommandSendResponse(topic=res.topic, payload=res.payload)


@router.post(
    "/robots/{robot_id}/commands/cancel",
    response_model=CommandSendResponse,
    summary="Send cancel command via backend (publishes to MQTT)",
)
async def send_cancel(
    req: CancelRequest,
    request: Request,
    robot_id: str = Path(..., description="Robot ID"),
) -> CommandSendResponse:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")

    ts = req.timestamp or datetime.now(timezone.utc).isoformat()

    facade = getattr(request.app.state, "navigation_facade", None)
    if facade is None:
        raise HTTPException(status_code=503, detail={"error": {"type": "NotReady", "msg": "Navigation service not ready"}})

    try:
        res = await facade.send_cancel(command_id=req.command_id, timestamp=ts)
    except MqttUnavailableError as e:
        raise HTTPException(status_code=503, detail={"error": {"type": "MQTTUnavailable", "msg": str(e)}})
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": {"type": "CommandDeliveryFailed", "msg": str(e)}})

    return CommandSendResponse(topic=res.topic, payload=res.payload)


@router.put(
    "/robots/{robot_id}/move/speed",
    summary="Телеуправление: команда скорости (джойстик/клавиатура)",
    description="Проксирует на Symovo PUT /v0/agv/{id}/move/speed. Один запрос — одна команда speed/angular_speed/duration.",
)
@router.post(
    "/robots/{robot_id}/move/speed",
    summary="Телеуправление: команда скорости (джойстик/клавиатура)",
    description="Проксирует на Symovo PUT /v0/agv/{id}/move/speed. Один запрос — одна команда speed/angular_speed/duration.",
)
async def move_speed(
    req: MoveSpeedRequest,
    request: Request,
    robot_id: str = Path(..., description="Robot ID"),
) -> Dict[str, Any]:
    if robot_id != settings.robot_id:
        raise HTTPException(status_code=404, detail=f"Robot {robot_id} not found")
    symovo = getattr(request.app.state, "symovo_client", None)
    if symovo is None:
        raise HTTPException(status_code=503, detail={"error": {"type": "NotReady", "msg": "Symovo client not ready"}})
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
