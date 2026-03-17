from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from db.trajectory import get_trajectory, save_trajectory
from db.robot_positions import get_robot_positions_list, get_robot_position, get_robot_position_by_name, save_robot_position, delete_robot_position
from app.xarm_status import get_velocity_percent, set_velocity_percent
from models.api_types import (
    DefaultMoveRequest,
    RobotMoveRequest,
    DepthQueryResponse,
    DepthQueryRequest,
    RobotTransportPositionResult,
    RobotMoveResult,
    RobotMoveBoxResult,
    RobotSystemStatus,
    RobotAsyncResponse,
    RobotActionResponse,
    RobotErrorResponse,
    TaskStatusResponse,
    SymovoTeleopConfigResponse,
    SymovoTeleopConfigUpdate,
    SymovoDriveModeRequest,
    SymovoDriveModeResponse,
    SYMOVO_TELEOP_DURATION_MIN,
    SYMOVO_TELEOP_DURATION_MAX,
    SYMOVO_TELEOP_LINEAR_MIN,
    SYMOVO_TELEOP_LINEAR_MAX,
    SYMOVO_TELEOP_ANGULAR_MIN,
    SYMOVO_TELEOP_ANGULAR_MAX,
    RobotPositionItem,
    RobotPositionRecordRequest,
    RobotPositionSavedResponse,
    RobotPositionRecordResponse,
    RobotPositionDeleteResponse,
)
from models.base_types import TaskStatus
from models.types import ActionResponse,JoystickCommand,JoystickFrame
import app.robot_scripts as robot
from typing import Any, Optional
from routes.decorators import safe_getter, tasked_getter, task_manager, check_safety_lockout
import asyncio
import json
import time
from fastapi import Request
import uuid
from types import SimpleNamespace
from app.config import (
    JOYSTICK_DEADZONE,
    JOYSTICK_DEFAULT_TTL_MS,
    JOYSTICK_MOVE_ACC,
    JOYSTICK_IS_MOVE_TOOL,
    JOYSTICK_MODE,
    SYMOVO_TELEOP_MOVE_URL,
    SYMOVO_DRIVE_MODE_URL,
    SAFETY_STATE_URL,
)
import aiohttp


router = APIRouter(tags=["AE.01 Robot"])
from typing import Callable, Awaitable, TypeVar, Any
from functools import wraps

T = TypeVar('T', bound=Callable[..., Awaitable[Any]])

from fastapi import HTTPException, status, Body

class _JoystickFrameValidator:
    def __init__(self, axes_len: int = 4, buttons_len: int = 19):
        self.axes_len = axes_len
        self.buttons_len = buttons_len

    def validate(self, payload: dict) -> dict:
        ts = float(payload.get("ts", time.time()))
        axes = payload.get("axes", [])
        buttons = payload.get("buttons", [])
        ttl = float(payload.get("ttl", 150))  # milliseconds
        if not isinstance(axes, list) or len(axes) != self.axes_len:
            raise ValueError("axes must be an array of length 4")
        if not isinstance(buttons, list) or len(buttons) != self.buttons_len:
            raise ValueError("buttons must be an array of length 19")
        return {"ts": ts, "axes": axes, "buttons": buttons, "ttl": ttl}

_validator = _JoystickFrameValidator()

DEADZONE = JOYSTICK_DEADZONE

def _apply_deadzone(value: float, dz: float = DEADZONE) -> float:
    return value if abs(value) >= dz else 0.0


def _clamp_ttl(ttl_value: Optional[float]) -> int:
    ttl = JOYSTICK_DEFAULT_TTL_MS if ttl_value is None else float(ttl_value)
    ttl = max(50.0, min(500.0, ttl))
    return int(ttl)


def _prepare_frame_payload(payload: dict) -> tuple[Optional[dict], Optional[str]]:
    ttl = _clamp_ttl(payload.get("ttl"))
    data = {
        "ts": float(payload.get("ts", time.time())),
        "axes": payload.get("axes", []),
        "buttons": payload.get("buttons", []),
        "ttl": ttl,
    }
    try:
        validated = _validator.validate(data)
        return validated, None
    except Exception as exc:
        return None, f"validation_error: {exc}"


def _enqueue_frame(app_state: Any, frame: dict, *, source: str) -> tuple[bool, Optional[str]]:
    ingress = getattr(app_state, "joystick_ingress", None)
    if ingress is not None:
        return ingress.submit(frame, source=source)

    scheduler = getattr(app_state, "joystick_scheduler", None)
    if scheduler is not None:
        return scheduler.submit(frame, source=source)

    jp = getattr(app_state, "joystick_pipeline", None)
    if jp is None:
        return False, "joystick_pipeline_unavailable"
    ok = jp.submit(frame)
    return bool(ok), None if ok else "queue_full"

# Default params template (used for save/run merge)
DEFAULT_POSITION_PARAMS = {
    "location": {"x_m": 0.0, "y_m": 0.0, "theta_deg": 0.0, "map_id": 0},
    "lift_position_cm": 0.0,
    "manipulator_offsets": {"x_offset_mm": 0.0, "y_offset_mm": 0.0, "z_offset_mm": 0.0},
    "xarm_joints": None,
    "velocity_percent": 0.0,
    "reset_faults": False,
}

def _deep_merge(defaults: dict, updates: dict) -> dict:
    out = dict(defaults)
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def _to_obj(params_dict: dict) -> SimpleNamespace:
    loc = params_dict.get("location") or {}
    mo = params_dict.get("manipulator_offsets") or {}
    xj = params_dict.get("xarm_joints") or {}
    joints = xj.get("joints") or {} if xj else {}
    return SimpleNamespace(
        location=SimpleNamespace(
            x_m=float(loc.get("x_m", 0.0) or 0.0),
            y_m=float(loc.get("y_m", 0.0) or 0.0),
            theta_deg=float(loc.get("theta_deg", 0.0) or 0.0),
            map_id=int(loc.get("map_id", 0) or 0),
        ),
        lift_position_cm=float(params_dict.get("lift_position_cm", 0.0) or 0.0),
        manipulator_offsets=SimpleNamespace(
            x_offset_mm=float(mo.get("x_offset_mm", 0.0) or 0.0),
            y_offset_mm=float(mo.get("y_offset_mm", 0.0) or 0.0),
            z_offset_mm=float(mo.get("z_offset_mm", 0.0) or 0.0),
        ) if (mo is not None) else None,
        xarm_joints=SimpleNamespace(
            name=xj.get("name", ""),
            joints=SimpleNamespace(
                j1=float(joints.get("j1", 0.0)),
                j2=float(joints.get("j2", 0.0)),
                j3=float(joints.get("j3", 0.0)),
                j4=float(joints.get("j4", 0.0)),
                j5=float(joints.get("j5", 0.0)),
                j6=float(joints.get("j6", 0.0)),
            ),
        ) if joints else None,
        velocity_percent=float(params_dict.get("velocity_percent", 0.0) or 0.0),
        reset_faults=bool(params_dict.get("reset_faults", False)),
        product_id=params_dict.get("product_id"),
    )

@router.post(
    "/joystick/frame",
    response_model=ActionResponse,
    summary="Send single joystick frame (HTTP)",
    description="HTTP frame with ts, axes[4], buttons[19], ttl(ms). Enqueues frame for background processing.",
)
@safe_getter(ActionResponse)
async def joystick_frame(request: Request, frame: JoystickFrame):
    await check_safety_lockout()
    payload = {"ts": frame.ts, "axes": frame.axes, "buttons": frame.buttons, "ttl": frame.ttl}
    data, error_msg = _prepare_frame_payload(payload)
    if error_msg:
        return ActionResponse(success=False, error=error_msg)
    success, enqueue_error = _enqueue_frame(request.app.state, data, source="http")
    return ActionResponse(success=success, message=None if success else enqueue_error)

@router.websocket("/joystick/connect")
async def joystick_connect(websocket: WebSocket):
    await websocket.accept()
    app_state = websocket.app.state

    # Safety lockout check — reject WS early if E-Stop active
    try:
        await check_safety_lockout()
    except Exception as exc:
        await websocket.send_json(
            {"type": "ack", "seq": None, "success": False, "error": f"safety_lockout: {exc}"}
        )
        await websocket.close(code=1008)
        return

    if getattr(app_state, "joystick_pipeline", None) is None:
        await websocket.send_json(
            {"type": "ack", "seq": None, "success": False, "error": "joystick_pipeline_unavailable"}
        )
        await websocket.close(code=1011)
        return

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                await websocket.send_json(
                    {"type": "ack", "seq": None, "success": False, "error": f"invalid_payload: {exc}"}
                )
                continue

            seq = message.get("seq")
            data, error_msg = _prepare_frame_payload(message)
            if error_msg:
                await websocket.send_json(
                    {"type": "ack", "seq": seq, "success": False, "error": error_msg, "server_ts": time.time()}
                )
                continue

            success, enqueue_error = _enqueue_frame(app_state, data, source="ws")
            await websocket.send_json(
                {
                    "type": "ack",
                    "seq": seq,
                    "success": success,
                    "error": enqueue_error,
                    "server_ts": time.time(),
                }
            )
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def _schedule_hold_stop(*args, **kwargs):
    # Deprecated: handled by background pipeline now
    return None


@router.post(
    "/move/to_product",
    response_model = RobotActionResponse,
    summary="Move robot to product location",
    description="""
 robot is busy or a device fails, the call returns an error (status 423/500).
""",
    response_description="Result of coordinated movement",
    responses={
        200: {"description": "Movement executed successfully"},
        422: {"description": "Validation error in request parameters"},
        202: {"description": "Robot is busy with another operation"},
        500: {"description": "Failed to move due to error in one of the subsystems"}
    }
)
@tasked_getter(RobotActionResponse)
async def move_to_product(params: RobotMoveRequest):
    result = await robot.move_robot_to_product(params)
    return ActionResponse(success=bool(result), message=None if result else "Failed")

@router.post(
    "/move/to_box_1",
    response_model= RobotActionResponse,
    summary="Move robot to Box 1",
    description="""
""",
    response_description="Result of movement to Box 1",
    responses={
        200: {
            "description": "Product placed in Box 1 successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "igus_result": {
                            "success": True,
                            "position": 30,
                            "details": "Lift moved to position 30"
                        },
                        "manipulator_result": {
                            "success": True,
                            "details": "Manipulator executed drop-off sequence"
                        },
                        "message": ""
                    }
                }
            }
        },
        422: {
            "description": "Validation error in request parameters",
        },
        423: {
            "description": "Robot is busy with another operation",
        },
        500: {
            "description": "Failed to move due to error in one of the subsystems.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "igus_result": {
                            "success": False,
                            "error": "Igus move failed"
                        },
                        "manipulator_result": None,
                        "message": "Igus move failed: Homing required"
                    }
                }
            }
        },
        503: {
            "description": "Robot subsystem is unavailable",
        }
    }
)
@tasked_getter(RobotActionResponse)
async def move_to_box_1(params: DefaultMoveRequest):
    result = await robot.move_robot_to_box_1(params.velocity_percent)
    return ActionResponse(success=bool(result))

@router.post(
    "/move/to_box_2",
    response_model = RobotActionResponse,
    summary="Move robot to Box 2",
    description="""
""",
    response_description="Result of movement to Box 2",
    responses={
        200: {
            "description": "Product placed in Box 2 successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "igus_result": {
                            "success": True,
                            "position": 30,
                            "details": "Lift moved to position 30"
                        },
                        "manipulator_result": {
                            "success": True,
                            "details": "Manipulator executed drop-off sequence"
                        },
                        "message": ""
                    }
                }
            }
        },
        422: {
            "description": "Validation error in request parameters",
        },
        423: {
            "description": "Robot is busy with another operation",
        },
        500: {
            "description": "Failed to move due to error in one of the subsystems.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "igus_result": {
                            "success": False,
                            "error": "Igus move failed"
                        },
                        "manipulator_result": None,
                        "message": "Igus move failed: Homing required"
                    }
                }
            }
        },
        503: {
            "description": "Robot subsystem is unavailable",
        }
    }
)
@tasked_getter(RobotActionResponse)
async def move_to_box_2(params: DefaultMoveRequest):
    result = await robot.move_robot_to_box_2(params.velocity_percent)
    return ActionResponse(success=bool(result))

@router.post(
    "/move/to_transport_position",
    response_model=  RobotActionResponse,
    summary="Move robot to transport position",
    description="""
""",
    response_description="Result of moving to transport position",
    responses={
        200: {
            "description": "Transport position reached successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "igus_result": {
                            "success": True,
                            "position": 20,
                            "details": "Lift moved to intermediate position"
                        },
                        "manipulator_result": {
                            "success": True,
                            "details": "Manipulator moved to transport waypoints"
                        },
                        "igus_final_result": {
                            "success": True,
                            "position": 0,
                            "details": "Lift moved to base position"
                        },
                        "message": ""
                    }
                }
            }
        },
        500: {
            "description": "Failed to move due to error in one of the subsystems.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "igus_result": {
                            "success": False,
                            "error": "Igus move failed"
                        },
                        "manipulator_result": None,
                        "igus_final_result": None,
                        "message": "Igus move failed: Homing required"
                    }
                }
            }
        }
    }
)
@tasked_getter(RobotActionResponse)
async def transport_position(params: DefaultMoveRequest):
    result = await robot.move_to_transport_position(params.velocity_percent)
    return ActionResponse(success=bool(result))

@router.get(
    "/status",
    response_model = RobotSystemStatus,
    summary="Get robot system status",
    description="Returns full status of all robot subsystems. Response is always a flat structure with 'ready' and 'message' in root.",
    response_description="Full status of the robot cell."
)
async def check_devices_ready() -> RobotSystemStatus:
    result = await robot.get_robot_system_status()
    return RobotSystemStatus(**result)

@router.get(
    "/symovo_teleop_config",
    response_model=SymovoTeleopConfigResponse,
    summary="AGV teleop parameters (joystick)",
    description=(
        "Current AGV motion parameters when using joystick buttons 4=W, 6=S, 7=A, 5=D. "
        "Used when sending commands to SYMOVO_TELEOP_MOVE_URL (e.g. :7906/move/speed). "
        "Allowed ranges: duration 0.05–2.0 s, linear speed 0.01–1.0 m/s, angular speed 0.01–2.0 rad/s."
    ),
    response_description="Current values: duration (s), linear speed (m/s), angular speed (rad/s).",
)
async def get_symovo_teleop_config(request: Request) -> SymovoTeleopConfigResponse:
    move_url = (SYMOVO_TELEOP_MOVE_URL or "").strip().rstrip("/")
    if not move_url.endswith("/move/speed"):
        raise HTTPException(status_code=503, detail="symovo_teleop_config_unavailable")
    cfg_url = f"{move_url[:-len('/move/speed')]}/teleop/config"
    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(cfg_url) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise HTTPException(status_code=503, detail=f"teleop_config_backend_error_{resp.status}: {raw}")
                data = json.loads(raw) if raw else {}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"teleop_config_backend_unavailable: {e}")

    return SymovoTeleopConfigResponse(
        duration=min(max(float(data.get("duration", 0.25)), SYMOVO_TELEOP_DURATION_MIN), SYMOVO_TELEOP_DURATION_MAX),
        linear_m_s=min(max(float(data.get("linear_m_s", 0.1)), SYMOVO_TELEOP_LINEAR_MIN), SYMOVO_TELEOP_LINEAR_MAX),
        angular_rad_s=min(max(float(data.get("angular_rad_s", 0.5)), SYMOVO_TELEOP_ANGULAR_MIN), SYMOVO_TELEOP_ANGULAR_MAX),
    )


@router.put(
    "/symovo_teleop_config",
    response_model=SymovoTeleopConfigResponse,
    summary="Update AGV teleop parameters",
    description=(
        "Partial update: only provided fields are applied, the rest are unchanged. "
        "Allowed ranges are the same as for GET /symovo_teleop_config."
    ),
    response_description="Current parameter values after update.",
)
async def put_symovo_teleop_config(
    request: Request, body: SymovoTeleopConfigUpdate
) -> SymovoTeleopConfigResponse:
    move_url = (SYMOVO_TELEOP_MOVE_URL or "").strip().rstrip("/")
    if not move_url.endswith("/move/speed"):
        raise HTTPException(status_code=503, detail="symovo_teleop_config_unavailable")
    cfg_url = f"{move_url[:-len('/move/speed')]}/teleop/config"
    payload: dict[str, float] = {}
    if body.duration is not None:
        payload["duration"] = float(body.duration)
    if body.linear_m_s is not None:
        payload["linear_m_s"] = float(body.linear_m_s)
    if body.angular_rad_s is not None:
        payload["angular_rad_s"] = float(body.angular_rad_s)

    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(cfg_url, json=payload) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise HTTPException(status_code=503, detail=f"teleop_config_backend_error_{resp.status}: {raw}")
                data = json.loads(raw) if raw else {}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"teleop_config_backend_unavailable: {e}")

    return SymovoTeleopConfigResponse(
        duration=min(max(float(data.get("duration", 0.25)), SYMOVO_TELEOP_DURATION_MIN), SYMOVO_TELEOP_DURATION_MAX),
        linear_m_s=min(max(float(data.get("linear_m_s", 0.1)), SYMOVO_TELEOP_LINEAR_MIN), SYMOVO_TELEOP_LINEAR_MAX),
        angular_rad_s=min(max(float(data.get("angular_rad_s", 0.5)), SYMOVO_TELEOP_ANGULAR_MIN), SYMOVO_TELEOP_ANGULAR_MAX),
    )


@router.put(
    "/symovo_drive_mode",
    response_model=SymovoDriveModeResponse,
    summary="Enable or disable AGV drive mode",
    description=(
        "Proxy to PUT /drive_mode?enable=true|false on the nav2adapter main API (port 7905). "
        "Drive mode must be enabled (enable=true) or the robot will not respond to joystick teleop. "
        "When enable=true the backend deactivates the charging station if needed. "
        "See symovo_teleop.txt §2.2."
    ),
    response_description="Success, requested enable value, and on success the backend response in result.",
)
async def put_symovo_drive_mode(body: SymovoDriveModeRequest) -> SymovoDriveModeResponse:
    if not SYMOVO_DRIVE_MODE_URL or not SYMOVO_DRIVE_MODE_URL.strip():
        raise HTTPException(
            status_code=503,
            detail="drive_mode service unavailable: SYMOVO_DRIVE_MODE_URL is not set.",
        )
    base = SYMOVO_DRIVE_MODE_URL.rstrip("/").rstrip("?")
    url = f"{base}?enable={'true' if body.enable else 'false'}"
    try:
        timeout = aiohttp.ClientTimeout(total=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(url) as resp:
                raw = await resp.text()
                data: Any = None
                if raw:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = raw
                if resp.status >= 400:
                    return SymovoDriveModeResponse(
                        success=False,
                        enable=body.enable,
                        result=data if resp.status < 500 else None,
                        detail=f"Backend returned {resp.status}: {data if isinstance(data, str) else str(data)}",
                    )
                return SymovoDriveModeResponse(
                    success=True,
                    enable=body.enable,
                    result=data,
                    detail=None,
                )
    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error calling drive_mode: {e!s}",
        )


@router.get(
    "/health/details",
    summary="Extended health diagnostics",
    description="Lightweight health info: joystick queue size, worker running flag.",
)
async def health_details(request: Request) -> Any:
    jp = getattr(request.app.state, "joystick_pipeline", None)
    ingress = getattr(request.app.state, "joystick_ingress", None)
    scheduler = getattr(request.app.state, "joystick_scheduler", None)
    interpreter = getattr(jp, "_interpreter", None) if jp else None
    return {
        "joystick_worker_running": bool(jp.is_running()) if jp else False,
        "joystick_queue_size": int(jp.queue_size()) if jp else 0,
        "joystick_ingress_running": bool(ingress.is_running()) if ingress else False,
        "joystick_ingress_queue_size": int(ingress.queue_size()) if ingress else 0,
        "joystick_ingress_enqueued": int(getattr(ingress, "enqueued_total", 0)) if ingress else 0,
        "joystick_ingress_dropped": int(getattr(ingress, "dropped_total", 0)) if ingress else 0,
        "joystick_scheduler_running": bool(scheduler.is_running()) if scheduler else False,
        "joystick_scheduler_queue_size": int(scheduler.queue_size()) if scheduler else 0,
        "joystick_scheduler_enqueued": int(getattr(scheduler, "enqueued_total", 0)) if scheduler else 0,
        "joystick_scheduler_dropped": int(getattr(scheduler, "dropped_total", 0)) if scheduler else 0,
        "joystick_interpreter_commands_total": int(getattr(interpreter, "commands_total", 0)) if interpreter else 0,
        "joystick_interpreter_last_command": getattr(interpreter, "last_command_type", None) if interpreter else None,
        "joystick_interpreter_frame_delay_ms": getattr(interpreter, "frame_to_command_delay_ms", 0.0)
        if interpreter
        else 0.0,
    }

@router.post(
    "/set_ready",
    response_model = RobotActionResponse,
    summary="Set robot system ready",
    description="Sets robot system ready for operation, resets all faults and references all devices",
    response_description="Result of setting robot system ready"
)
@tasked_getter(RobotActionResponse)
async def set_robot_ready() -> ActionResponse:
    result = await robot.set_ready()
    return ActionResponse(success=bool(result))

@router.get("/autotake_config/trajectory")
def api_get_trajectory():
    config = get_trajectory()
    if config is None:
        raise HTTPException(status_code=404, detail="Trajectory configuration not found.")
    return config

@router.post("/autotake_config/trajectory", status_code=status.HTTP_201_CREATED)
def api_save_trajectory(config: dict):
    try:
        save_trajectory(config)
        return {"status": "ok", "message": "Trajectory configuration saved."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/waypoints/list",
    response_model=list[RobotPositionItem],
    summary="List saved waypoints",
    description="Returns all saved waypoints ordered by creation time (newest first).",
)
def api_get_robot_positions_list():
    result = get_robot_positions_list()
    return result


async def _capture_and_save(name: str, velocity_percent: float) -> RobotPositionRecordResponse:
    """Reads current state from all devices and saves as a named position."""
    snapshot = await robot.record_current_position()

    lift_data    = snapshot.get("lift") or {}
    joints_data  = snapshot.get("xarm_joints") or {}
    vehicle_data = snapshot.get("vehicle_pose") or {}

    lift_raw = float(lift_data.get("position", lift_data.get("position_cm", 0.0)) or 0.0)
    lift_cm  = lift_raw / 1000.0   # encoder units → cm

    location = {
        "x_m":       float(vehicle_data.get("x_m", 0.0) or 0.0),
        "y_m":       float(vehicle_data.get("y_m", 0.0) or 0.0),
        "theta_deg": float(vehicle_data.get("theta_deg", 0.0) or 0.0),
        "map_id":    int(vehicle_data.get("map_id", 0) or 0),
    }

    params = _deep_merge(DEFAULT_POSITION_PARAMS, {
        "location":        location,
        "lift_position_cm": lift_cm,
        "velocity_percent": velocity_percent,
        "xarm_joints":     joints_data,
    })

    position_id = uuid.uuid4().hex[:8]
    save_robot_position({"id": position_id, "name": name, "params": params})

    return RobotPositionRecordResponse(
        status="ok",
        message="Position saved.",
        id=position_id,
        snapshot=snapshot,
    )


@router.post(
    "/waypoints/save",
    status_code=status.HTTP_201_CREATED,
    response_model=RobotPositionRecordResponse,
    summary="Save current waypoint",
    description=(
        "Reads AGV coordinates, lift height and arm joints from devices, "
        "saves as a named waypoint. Only `name` is required from the user."
    ),
)
async def api_save_robot_position(body: RobotPositionRecordRequest = Body(default=RobotPositionRecordRequest())):
    try:
        return await _capture_and_save(body.name or "", body.velocity_percent)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/waypoints/record",
    status_code=status.HTTP_201_CREATED,
    response_model=RobotPositionRecordResponse,
    summary="Record current waypoint",
    description=(
        "Reads AGV coordinates, lift height and arm joints from devices, "
        "saves as a named waypoint. Only `name` is required from the user."
    ),
)
async def api_record_robot_position(body: RobotPositionRecordRequest = Body(default=RobotPositionRecordRequest())):
    try:
        return await _capture_and_save(body.name or "", body.velocity_percent)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/waypoints/run",
    status_code=status.HTTP_201_CREATED,
    response_model=RobotActionResponse,
    summary="Send robot to waypoint",
    description=(
        "Runs `move_robot_to_product` for the saved waypoint. "
        "Returns a task_id — poll `/tasks/status/{task_id}` to track progress. "
        "Cancel with `POST /tasks/cancel/{task_id}`."
    ),
)
@tasked_getter(RobotActionResponse)
async def run_robot_position(position_id: str):
    try:
        robot_position = get_robot_position(position_id)
        if robot_position is None:
            raise HTTPException(status_code=404, detail="Robot position not found.")
        params = robot_position.get("params")
        if not isinstance(params, dict):
            raise HTTPException(status_code=400, detail="Robot position params not found or invalid.")
        merged = _deep_merge(DEFAULT_POSITION_PARAMS, params)
        params_obj = _to_obj(merged)
        ok = await robot.move_robot_to_product(params_obj)
        return ActionResponse(success=bool(ok), message=None if ok else "Failed to run robot position.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class _WaypointUpdateBody(BaseModel):
    name: Optional[str] = None
    velocity_percent: Optional[float] = None

@router.put("/waypoints/update", summary="Update waypoint name / velocity")
def api_update_robot_position(position_id: str, body: _WaypointUpdateBody):
    pos = get_robot_position(position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail="Waypoint not found")
    params = dict(pos.get("params") or {})
    if body.velocity_percent is not None:
        params["velocity_percent"] = body.velocity_percent
    new_name = body.name if body.name is not None else (pos.get("name") or "")
    save_robot_position({"id": position_id, "name": new_name, "params": params})
    return {"status": "ok", "id": position_id}


class _SettingsBody(BaseModel):
    velocity_percent: float

@router.get("/settings", summary="Get global robot settings")
def api_get_settings():
    return {"velocity_percent": get_velocity_percent()}

@router.put("/settings", summary="Update global robot settings")
def api_put_settings(body: _SettingsBody):
    set_velocity_percent(body.velocity_percent)
    return {"velocity_percent": get_velocity_percent()}


@router.post(
    "/waypoints/delete",
    status_code=status.HTTP_201_CREATED,
    response_model=RobotPositionDeleteResponse,
    summary="Delete waypoint",
)
def api_delete_robot_position(position_id: str):
    try:
        delete_robot_position(position_id)
        return RobotPositionDeleteResponse(status="ok", message="Robot position deleted.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class NavigateRequest(BaseModel):
    command_id: str
    target_id: str
    priority: str = "normal"
    metadata: Optional[dict] = None
    timestamp: Optional[str] = None


@router.post(
    "/tasks/navigate",
    response_model=RobotActionResponse,
    summary="Navigate robot to target (orchestrated)",
    description=(
        "Robot-level navigation: resolves target_id from saved waypoints, "
        "then orchestrates AGV + lift + arm movement via move_robot_to_product. "
        "Returns a task_id for polling via /tasks/status/{task_id}."
    ),
)
@tasked_getter(RobotActionResponse)
async def tasks_navigate(req: NavigateRequest):
    position = get_robot_position_by_name(req.target_id)
    if position is None:
        raise HTTPException(status_code=404, detail=f"Unknown target_id: {req.target_id}")
    params = position.get("params")
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="Invalid position params")
    merged = _deep_merge(DEFAULT_POSITION_PARAMS, params)
    params_obj = _to_obj(merged)
    ok = await robot.move_robot_to_product(params_obj)
    return ActionResponse(success=bool(ok), message=None if ok else "Navigation failed")


class GoToChargingRequest(BaseModel):
    station_id: int


@router.post(
    "/tasks/go_to_charging_station",
    response_model=RobotActionResponse,
    summary="Go to charging station (orchestrated)",
    description=(
        "Cancels any running task, prepares robot (arm→JOB_POSE, lift down), "
        "clears transports, activates charger. Symovo handles docking automatically."
    ),
)
@tasked_getter(RobotActionResponse)
async def tasks_go_to_charging_station(req: GoToChargingRequest):
    ok = await robot.go_to_charging_station(req.station_id)
    return ActionResponse(success=bool(ok), message=None if ok else "Charging failed")


@router.post(
    "/move/autotake",
    response_model = RobotActionResponse,
    summary="Autotake product (experimental)",
    description="""
    Experimental feature. Not implemented in this build.
""",
    response_description="Autotake is not available",
    responses={
        503: {"description": "Autotake not implemented"}
    }
)
@tasked_getter(RobotActionResponse)
async def autotake(params: DefaultMoveRequest):
    result = await robot.autotake(params.velocity_percent)
    return ActionResponse(success=bool(result))

# ----------------------------
# Task status endpoints
# ----------------------------

@router.get("/tasks/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    return task_manager.status(task_id)

@router.get("/tasks/current")
async def get_current_task():
    tid = task_manager.current_id()
    if not tid:
        return {"task_id": None, "status": TaskStatus.NOT_FOUND, "result": None}
    s = task_manager.status(tid)
    return {"task_id": tid, "status": s.status, "result": s.result}

@router.post("/tasks/cancel/{task_id}", response_model=TaskStatusResponse)
async def cancel_task(task_id: str):
    return await task_manager.cancel(task_id)

@router.post("/tasks/cancel_current", response_model=TaskStatusResponse)
async def cancel_current_task():
    tid = task_manager.current_id()
    return await task_manager.cancel(tid) if tid else TaskStatusResponse(status=TaskStatus.NOT_FOUND, result=None)


# ----------------------------
# Safety recovery
# ----------------------------

import logging as _logging
_safety_log = _logging.getLogger("robot_service.safety_recover")


@router.post(
    "/safety/recover",
    summary="Attempt sequential recovery from safety lockout",
    description=(
        "After E-Stop release / safety relay restoration, call this endpoint to "
        "clear faults on all subsystems and re-enable motion. Steps: "
        "1) verify safety relay closed, 2) igus fault_reset, "
        "3) xArm recover + enable_motion, 4) drive_mode enable."
    ),
)
async def safety_recover():
    steps: list[dict] = []

    # Step 1 — verify safety relay is closed
    relay_ok = False
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(SAFETY_STATE_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("safety_lockout"):
                        reason = data.get("reason", "safety lockout active")
                        steps.append({"name": "check_safety_relay", "status": "failed", "error": reason})
                        return {"success": False, "steps": steps, "message": "Safety relay still open — release E-Stop first"}
                    relay_ok = True
                    steps.append({"name": "check_safety_relay", "status": "ok"})
                else:
                    steps.append({"name": "check_safety_relay", "status": "skipped", "reason": f"nav2adapter returned {resp.status}"})
    except Exception as exc:
        steps.append({"name": "check_safety_relay", "status": "skipped", "reason": str(exc)})

    # Step 2 — igus fault_reset
    try:
        await robot.lift.fault_reset()
        steps.append({"name": "igus_fault_reset", "status": "ok"})
    except Exception as exc:
        _safety_log.warning("igus fault_reset failed: %s", exc)
        steps.append({"name": "igus_fault_reset", "status": "failed", "error": str(exc)})

    # Step 3 — xArm recover + enable_motion
    try:
        await robot.manipulator.fault_reset()
        steps.append({"name": "xarm_recover", "status": "ok"})
    except Exception as exc:
        _safety_log.warning("xarm recover failed: %s", exc)
        steps.append({"name": "xarm_recover", "status": "failed", "error": str(exc)})

    try:
        await robot.manipulator.enable_motion()
        steps.append({"name": "xarm_enable_motion", "status": "ok"})
    except Exception as exc:
        _safety_log.warning("xarm enable_motion failed: %s", exc)
        steps.append({"name": "xarm_enable_motion", "status": "failed", "error": str(exc)})

    # Step 4 — drive_mode enable
    if SYMOVO_DRIVE_MODE_URL:
        try:
            url = f"{SYMOVO_DRIVE_MODE_URL.rstrip('/').rstrip('?')}?enable=true"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.put(url) as resp:
                    if resp.status < 400:
                        steps.append({"name": "drive_mode_enable", "status": "ok"})
                    else:
                        body = await resp.text()
                        steps.append({"name": "drive_mode_enable", "status": "failed", "error": f"HTTP {resp.status}: {body[:200]}"})
        except Exception as exc:
            _safety_log.warning("drive_mode enable failed: %s", exc)
            steps.append({"name": "drive_mode_enable", "status": "failed", "error": str(exc)})
    else:
        steps.append({"name": "drive_mode_enable", "status": "skipped", "reason": "SYMOVO_DRIVE_MODE_URL not set"})

    all_ok = all(s["status"] == "ok" for s in steps)
    _safety_log.info("Safety recovery %s: %s", "succeeded" if all_ok else "partial", steps)
    return {"success": all_ok, "steps": steps}


# ----------------------------
# Job-zone test endpoint
# ----------------------------

@router.post(
    "/move/test_job_zone",
    response_model=RobotActionResponse,
    summary="Test: traverse 8 job-zone corners",
    description=(
        "Moves the manipulator to JOB_POSE, reads current TCP orientation, "
        "then visits all 8 corners of the job-zone safe-box to verify "
        "Cartesian reachability. Returns per-corner results."
    ),
)
@tasked_getter(RobotActionResponse)
async def test_job_zone():
    result = await robot.test_job_zone_corners()
    return ActionResponse(success=result["all_ok"], message=str(result["corners"]))