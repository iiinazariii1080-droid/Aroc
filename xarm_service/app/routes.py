import uuid
from fastapi import APIRouter, HTTPException, Request
from typing import Optional, Dict, Any, List

from app.types import (
    XarmMoveWithJointsDictParams,
    XarmMoveWithJointsParams,
    MoveWithPoseParams as XarmMoveWithPoseParams,
    MoveWithToolParams as XarmMoveWithToolParams,
    ActionResponse,
    XarmJointsPositionResponse,
    XarmStatusResponse,
)
from app.decorator import safe_getter
from app.di import get_command_service

from drivers.xarm_driver.actor.commands import Command, CommandType, ExecutionPolicy, ResultStatus
from drivers.xarm_driver import xarm_positions

router = APIRouter(tags=["AE.01 (XArm)"])


async def _recover_if_requested(reset_faults: bool, request: Request) -> None:
    if not reset_faults:
        return
    svc = get_command_service()
    cmd = Command(
        command_id=f"{request.state.command_id}-recover",
        type=CommandType.RECOVER_FAULTS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    res = await svc.enqueue(cmd)
    if res.status != ResultStatus.SUCCEEDED:
        raise HTTPException(status_code=409, detail=res.error_message or "recovery failed")


@router.post("/complex_move/with_joints_dict", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def complex_move_with_joints_dict(params: XarmMoveWithJointsDictParams, request: Request):
    """Execute a sequence of joint waypoints (blocking). All waypoints go through Actor."""
    await _recover_if_requested(params.reset_faults, request)
    svc = get_command_service()
    for idx, pt in enumerate(params.points):
        cmd = Command(
            command_id=f"{request.state.command_id}-pt{idx}",
            type=CommandType.MOVE_JOINTS,
            params={
                "j1": pt.j1,
                "j2": pt.j2,
                "j3": pt.j3,
                "j4": pt.j4,
                "j5": pt.j5,
                "j6": pt.j6,
                "velocity_percent": params.velocity_percent,
            },
            policy=ExecutionPolicy.QUEUE,
        )
        result = await svc.enqueue(cmd)
        if result.status == ResultStatus.REJECTED:
            raise HTTPException(status_code=409, detail=result.error_message or "rejected")
        if result.status != ResultStatus.SUCCEEDED:
            raise HTTPException(
                status_code=422 if "OUT_OF_WORKSPACE" in (result.error_message or "") else 500,
                detail=result.error_message or "move failed",
            )
    return ActionResponse(success=True, message=None)


@router.post("/move/change_joints", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def change_joints(params: XarmMoveWithJointsParams, request: Request):
    """Move joints via CommandService -> RobotActor with envelope preflight."""
    await _recover_if_requested(params.reset_faults, request)
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.MOVE_JOINTS,
        params={
            "j1": params.j1,
            "j2": params.j2,
            "j3": params.j3,
            "j4": params.j4,
            "j5": params.j5,
            "j6": params.j6,
            "velocity_percent": params.velocity_percent,
        },
        policy=ExecutionPolicy.REJECT_IF_BUSY,
    )
    result = await svc.enqueue(cmd)
    if result.status == ResultStatus.REJECTED:
        raise HTTPException(status_code=409, detail=result.error_message or "rejected")
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(
            status_code=422 if "OUT_OF_WORKSPACE" in (result.error_message or "") else 500,
            detail=result.error_message or "move failed",
        )
    return ActionResponse(success=True, message=None)


@router.post("/move/change_pose", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def change_pose(params: XarmMoveWithPoseParams, request: Request):
    """Move to a named pose (mapped to joints) through Actor."""
    await _recover_if_requested(params.reset_faults, request)
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.MOVE_POSE,
        params={
            "name": params.name,
            "velocity_percent": params.velocity_percent,
        },
        policy=ExecutionPolicy.REJECT_IF_BUSY,
    )
    result = await svc.enqueue(cmd)
    if result.status == ResultStatus.REJECTED:
        raise HTTPException(status_code=409, detail=result.error_message or "rejected")
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(
            status_code=422 if "OUT_OF_WORKSPACE" in (result.error_message or "") else 500,
            detail=result.error_message or "move failed",
        )
    return ActionResponse(success=True, message=None)


@router.post("/move/change_tool_position", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def change_tool_position(params: XarmMoveWithToolParams, request: Request):
    """Relative tool-frame move through Actor, with TCP workspace preflight."""
    await _recover_if_requested(params.reset_faults, request)
    svc = get_command_service()
    cmd_params_dict = {
        "x_offset_mm": params.x_offset_mm,
        "y_offset_mm": params.y_offset_mm,
        "z_offset_mm": params.z_offset_mm,
        "velocity_percent": params.velocity_percent,
    }
    if params.roll_offset_deg is not None:
        cmd_params_dict["roll_offset_deg"] = params.roll_offset_deg
    if params.pitch_offset_deg is not None:
        cmd_params_dict["pitch_offset_deg"] = params.pitch_offset_deg
    if params.yaw_offset_deg is not None:
        cmd_params_dict["yaw_offset_deg"] = params.yaw_offset_deg
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.MOVE_TOOL_POSITION,
        params=cmd_params_dict,
        policy=ExecutionPolicy.REJECT_IF_BUSY,
    )
    result = await svc.enqueue(cmd)
    if result.status == ResultStatus.REJECTED:
        raise HTTPException(status_code=409, detail=result.error_message or "rejected")
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(
            status_code=422 if "OUT_OF_WORKSPACE" in (result.error_message or "") else 500,
            detail=result.error_message or "move failed",
        )
    return ActionResponse(success=True, message=None)


@router.post("/gripper/take", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def gripper_take(request: Request):
    """Close/activate gripper through Actor."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GRIP_CLOSE,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(status_code=500, detail=result.error_message or "gripper failed")
    return ActionResponse(success=True, message=None)


@router.post("/gripper/drop", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def gripper_drop(request: Request):
    """Open/deactivate gripper through Actor."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GRIP_OPEN,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(status_code=500, detail=result.error_message or "gripper failed")
    return ActionResponse(success=True, message=None)


@router.post("/recover", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def recover(request: Request):
    """Explicit recovery: clean_error, clean_warn, set_state(0). Does NOT enable motion."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.RECOVER_FAULTS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    return ActionResponse(success=(result.status == ResultStatus.SUCCEEDED), message=result.error_message)


@router.post("/enable_motion", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def enable_motion(request: Request):
    """Explicit motion enable."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.ENABLE_MOTION,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    return ActionResponse(success=(result.status == ResultStatus.SUCCEEDED), message=result.error_message)


@router.post("/disable_motion", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def disable_motion(request: Request):
    """Disable motion (stop drives)."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.DISABLE_MOTION,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    return ActionResponse(success=(result.status == ResultStatus.SUCCEEDED), message=result.error_message)


@router.post("/stop", response_model=ActionResponse)
@safe_getter(ActionResponse)
async def stop(request: Request):
    """Best-effort stop current motion."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.STOP,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    return ActionResponse(success=(result.status == ResultStatus.SUCCEEDED), message=result.error_message)


@router.get("/current_position", response_model=XarmJointsPositionResponse)
@safe_getter(XarmJointsPositionResponse)
async def get_current_position(request: Request):
    """Return nearest saved pose name to current joints (no direct SDK access)."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GET_STATUS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED or not result.telemetry_snapshot:
        raise HTTPException(status_code=503, detail=result.error_message or "status failed")
    angles = result.telemetry_snapshot.get("angles") or []
    if len(angles) < 6:
        raise HTTPException(status_code=503, detail="angles not available")
    current_position = {
        "name": "CURRENT",
        "joints": {"j1": float(angles[0]), "j2": float(angles[1]), "j3": float(angles[2]), "j4": float(angles[3]), "j5": float(angles[4]), "j6": float(angles[5])},
    }
    nearest = xarm_positions.find_closest_position(current_position)
    return XarmJointsPositionResponse(**nearest)


@router.get("/joints_position", response_model=XarmJointsPositionResponse)
@safe_getter(XarmJointsPositionResponse)
async def get_manipulator_joints_position(request: Request):
    """Return current joints (no direct SDK access)."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GET_STATUS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED or not result.telemetry_snapshot:
        raise HTTPException(status_code=503, detail=result.error_message or "status failed")
    angles = result.telemetry_snapshot.get("angles") or []
    if len(angles) < 6:
        raise HTTPException(status_code=503, detail="angles not available")
    return XarmJointsPositionResponse(
        name="CURRENT",
        joints={
            "j1": float(angles[0]),
            "j2": float(angles[1]),
            "j3": float(angles[2]),
            "j4": float(angles[3]),
            "j5": float(angles[4]),
            "j6": float(angles[5]),
        },
    )


@router.get("/status", response_model=XarmStatusResponse)
@safe_getter(XarmStatusResponse)
async def get_manipulator_status(request: Request):
    """Status via CommandService -> RobotActor (single-writer)."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GET_STATUS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED or result.telemetry_snapshot is None:
        raise HTTPException(
            status_code=503 if result.status == ResultStatus.REJECTED else 500,
            detail=result.error_message or "status failed",
        )
    snap = result.telemetry_snapshot
    err = snap.get("error_code", 0) or 0
    warn = snap.get("warn_code", 0) or 0
    state_code = snap.get("state", 0)
    motion_en = snap.get("motion_enabled", True)
    return XarmStatusResponse(
        alive=state_code in (1, 2, 6),
        connected=True,
        state_code=state_code,
        has_err_warn=(err != 0 or warn != 0),
        has_error=(err != 0),
        has_warn=(warn != 0),
        error_code=int(err),
        safety_lockout_hint=(state_code == 4 and err != 0 and not motion_en),
    )


# ── Smart Grasp endpoints ─────────────────────────────────────────────────

from pydantic import BaseModel, Field as PField

class SmartGraspRequest(BaseModel):
    target_x: Optional[int] = PField(None, description="Target pixel X (optional)")
    target_y: Optional[int] = PField(None, description="Target pixel Y (optional)")
    max_retries: Optional[int] = PField(None, description="Override default retry count")

class SmartGraspResponse(BaseModel):
    success: bool
    outcome: str
    attempts: int = 0
    detection: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    elapsed_s: float = 0.0

class DepthScanResponse(BaseModel):
    objects: List[Dict[str, Any]]
    count: int

class GripperStatusResponse(BaseModel):
    active: bool
    feedback: str
    vacuum_level: Optional[float] = None
    part_present: bool = False
    part_secured: bool = False
    energy_saving: bool = False
    motor_stall: bool = False
    pcb_temperature: int = 0
    membrane_hours: int = 0
    membrane_warn: bool = False
    sensor_supported: bool = True
    sensor_disabled_reason: Optional[str] = None
    activated_at: Optional[float] = None
    idle_elapsed_s: Optional[float] = None
    watchdog_enabled: bool = False
    watchdog_timeout_s: float = 0.0


@router.post("/gripper/smart_grasp", response_model=SmartGraspResponse)
@safe_getter(SmartGraspResponse)
async def smart_grasp(params: SmartGraspRequest, request: Request):
    """Full‑auto grasp: detect → align → approach → grip → verify → lift."""
    svc = get_command_service()
    cmd_params: Dict[str, Any] = {}
    if params.target_x is not None and params.target_y is not None:
        cmd_params["target_x"] = params.target_x
        cmd_params["target_y"] = params.target_y
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.SMART_GRASP,
        params=cmd_params,
        policy=ExecutionPolicy.REJECT_IF_BUSY,
        timeout_s=120.0,
    )
    result = await svc.enqueue(cmd)
    if result.status == ResultStatus.REJECTED:
        raise HTTPException(status_code=409, detail=result.error_message or "rejected")
    snap = result.telemetry_snapshot or {}
    return SmartGraspResponse(
        success=(result.status == ResultStatus.SUCCEEDED),
        outcome=snap.get("outcome", "UNKNOWN"),
        attempts=snap.get("attempts", 0),
        detection=snap.get("detection"),
        verification=snap.get("verification"),
        error_message=result.error_message,
        elapsed_s=snap.get("elapsed_s", 0.0),
    )


@router.post("/depth/scan", response_model=DepthScanResponse)
@safe_getter(DepthScanResponse)
async def depth_scan(request: Request):
    """Capture a depth frame and return detected objects."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.DEPTH_SCAN,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(status_code=500, detail=result.error_message or "scan failed")
    snap = result.telemetry_snapshot or {}
    return DepthScanResponse(
        objects=snap.get("objects", []),
        count=snap.get("count", 0),
    )


@router.get("/gripper/status", response_model=GripperStatusResponse)
@safe_getter(GripperStatusResponse)
async def gripper_status(request: Request):
    """Read current gripper/vacuum state."""
    svc = get_command_service()
    cmd = Command(
        command_id=request.state.command_id,
        type=CommandType.GRIPPER_STATUS,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    result = await svc.enqueue(cmd)
    if result.status != ResultStatus.SUCCEEDED:
        raise HTTPException(status_code=500, detail=result.error_message or "status failed")
    snap = result.telemetry_snapshot or {}
    return GripperStatusResponse(
        active=snap.get("active", False),
        feedback=snap.get("feedback", "UNKNOWN"),
        vacuum_level=snap.get("vacuum_level"),
        part_present=bool(snap.get("part_present", False)),
        part_secured=bool(snap.get("part_secured", False)),
        energy_saving=bool(snap.get("energy_saving", False)),
        motor_stall=bool(snap.get("motor_stall", False)),
        pcb_temperature=int(snap.get("pcb_temperature", 0) or 0),
        membrane_hours=int(snap.get("membrane_hours", 0) or 0),
        membrane_warn=bool(snap.get("membrane_warn", False)),
        sensor_supported=bool(snap.get("sensor_supported", True)),
        sensor_disabled_reason=snap.get("sensor_disabled_reason"),
        activated_at=snap.get("activated_at"),
        idle_elapsed_s=snap.get("idle_elapsed_s"),
        watchdog_enabled=snap.get("watchdog_enabled", False),
        watchdog_timeout_s=snap.get("watchdog_timeout_s", 0.0),
    )


# ── Grasp Analysis endpoints ──────────────────────────────────────────────

import dataclasses
import logging as _logging

from services.depth_service import get_depth_service
from services.grasp_analysis import GraspAnalysisService

_ga_logger = _logging.getLogger("routes.grasp_analysis")


class CaptureFrameResponse(BaseModel):
    frame_id: str
    timestamp_ms: int
    depth_at_center_mm: float


class AnalyzeGraspRequest(BaseModel):
    frame_id: Optional[str] = PField(None, description="Cached frame ID (omit for fresh capture)")
    target_x_norm: float = PField(50.0, ge=0, le=100, description="Target X as % of width")
    target_y_norm: float = PField(50.0, ge=0, le=100, description="Target Y as % of height")
    # Optional algorithm overrides (from trajectory config)
    yaw_search_step_deg: Optional[float] = PField(None, description="Yaw search step in degrees")
    collision_envelope_mm: Optional[float] = PField(None, description="Collision corridor envelope radius mm")
    seal_perimeter_points: Optional[int] = PField(None, description="Number of perimeter sample points for seal score")
    cup_grid_step_mm: Optional[float] = PField(None, description="Grid step for cup placement search mm")


class AnalyzeGraspResponse(BaseModel):
    success: bool
    status: str
    reason_codes: List[str] = []
    confidence: float = 0.0
    confidence_breakdown: Optional[Dict[str, Any]] = None
    roll_correction_deg: float = 0.0
    pitch_correction_deg: float = 0.0
    yaw_correction_deg: float = 0.0
    xy_correction_mm: List[float] = [0.0, 0.0]
    surface_normal: Optional[Dict[str, Any]] = None
    dual_cup_result: Optional[Dict[str, Any]] = None
    collision_risk: str = "CLEAR"
    approach_clear: bool = True
    collision_zones: List[Dict[str, Any]] = []
    frame_context: Optional[Dict[str, Any]] = None
    transform_info: Optional[Dict[str, Any]] = None


@router.post("/depth/capture_frame", response_model=CaptureFrameResponse)
@safe_getter(CaptureFrameResponse)
async def capture_frame(request: Request):
    """Capture a depth frame and cache it for subsequent analysis."""
    ds = get_depth_service()
    frame_id, frame, ctx = await ds.capture_frame()
    ci = ds.intrinsics
    center_depth = float(frame[ci.height // 2, ci.width // 2]) * ci.depth_scale
    return CaptureFrameResponse(
        frame_id=frame_id,
        timestamp_ms=ctx.timestamp_ms,
        depth_at_center_mm=center_depth,
    )


@router.post("/depth/analyze_grasp", response_model=AnalyzeGraspResponse)
@safe_getter(AnalyzeGraspResponse)
async def analyze_grasp(params: AnalyzeGraspRequest, request: Request):
    """Analyse a depth frame for optimal dual-cup grasp placement."""
    ds = get_depth_service()
    gas = GraspAnalysisService(ds)
    gas.set_overrides(
        yaw_search_step_deg=params.yaw_search_step_deg,
        collision_envelope_mm=params.collision_envelope_mm,
        seal_perimeter_points=params.seal_perimeter_points,
        cup_grid_step_mm=params.cup_grid_step_mm,
    )

    frame = None
    frame_reused = True

    if params.frame_id:
        frame = ds.get_cached_frame(params.frame_id)

    if frame is None:
        # Fresh capture (backward compat or expired frame_id)
        frame_reused = False
        frame_id, frame, ctx = await ds.capture_frame()
        ctx.frame_reused = False
        _ga_logger.info("analyze_grasp: fresh capture (frame_id=%s)", frame_id)
    else:
        from models.grasp_types import FrameContext
        import time as _time
        ci = ds.intrinsics
        ctx = FrameContext(
            frame_id=params.frame_id,
            timestamp_ms=int(_time.time() * 1000),
            intrinsics={"fx": ci.fx, "fy": ci.fy, "cx": ci.cx, "cy": ci.cy},
            depth_scale=ci.depth_scale,
            resolution=(ci.width, ci.height),
            frame_reused=True,
        )

    result = await gas.analyze(frame, params.target_x_norm, params.target_y_norm, ctx)

    # 3e — IK feasibility check on corrected pose
    if result.analysis_status.status != "FAILED":
        try:
            svc = get_command_service()
            # Get current TCP pose
            status_cmd = Command(
                command_id=f"{request.state.command_id}-ik-status",
                type=CommandType.GET_STATUS,
                params={},
                policy=ExecutionPolicy.QUEUE,
            )
            status_res = await svc.enqueue(status_cmd)
            tcp_pose = None
            if status_res.status == ResultStatus.SUCCEEDED and status_res.telemetry_snapshot:
                tcp_pose = status_res.telemetry_snapshot.get("position")

            if tcp_pose and len(tcp_pose) >= 6:
                # Build corrected target pose (approximate: add corrections to current TCP)
                target_pose = [
                    tcp_pose[0] + result.xy_correction_mm[0],
                    tcp_pose[1] + result.xy_correction_mm[1],
                    tcp_pose[2],  # Z stays (descent happens later)
                    tcp_pose[3] + result.roll_correction_deg,
                    tcp_pose[4] + result.pitch_correction_deg,
                    tcp_pose[5] + result.yaw_correction_deg,
                ]
                ik_cmd = Command(
                    command_id=f"{request.state.command_id}-ik-check",
                    type=CommandType.CHECK_IK,
                    params={"pose": target_pose},
                    policy=ExecutionPolicy.QUEUE,
                )
                ik_res = await svc.enqueue(ik_cmd)
                if ik_res.status == ResultStatus.SUCCEEDED and ik_res.telemetry_snapshot:
                    feasible = ik_res.telemetry_snapshot.get("feasible", True)
                    if not feasible:
                        result.confidence_breakdown.reachability_proxy = 0.0
                        result.confidence = (
                            result.confidence_breakdown.seal_proxy
                            * result.confidence_breakdown.stability_proxy
                            * result.confidence_breakdown.corridor_proxy
                            * 0.0
                        )
                        result.analysis_status.reason_codes.append("IK_INFEASIBLE")
                        if result.analysis_status.status == "OK":
                            result.analysis_status.status = "DEGRADED"
                        _ga_logger.warning("IK check: corrected pose infeasible %s", target_pose)
                    else:
                        _ga_logger.info("IK check: corrected pose feasible")
        except Exception as ik_exc:
            _ga_logger.warning("IK check skipped: %s", ik_exc)

    _ga_logger.info(
        "analyze_grasp: status=%s conf=%.3f roll=%.1f pitch=%.1f yaw=%.1f xy=(%.1f,%.1f)",
        result.analysis_status.status, result.confidence,
        result.roll_correction_deg, result.pitch_correction_deg, result.yaw_correction_deg,
        result.xy_correction_mm[0], result.xy_correction_mm[1],
    )

    def _dc(obj):
        """Recursively convert dataclass to dict."""
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {k: _dc(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, (list, tuple)):
            return [_dc(i) for i in obj]
        return obj

    return AnalyzeGraspResponse(
        success=result.analysis_status.status != "FAILED",
        status=result.analysis_status.status,
        reason_codes=result.analysis_status.reason_codes,
        confidence=result.confidence,
        confidence_breakdown=_dc(result.confidence_breakdown),
        roll_correction_deg=result.roll_correction_deg,
        pitch_correction_deg=result.pitch_correction_deg,
        yaw_correction_deg=result.yaw_correction_deg,
        xy_correction_mm=list(result.xy_correction_mm),
        surface_normal=_dc(result.surface_normal),
        dual_cup_result=_dc(result.dual_cup_result) if result.dual_cup_result else None,
        collision_risk=result.collision_risk,
        approach_clear=result.approach_clear,
        collision_zones=[_dc(z) for z in result.collision_zones],
        frame_context=_dc(result.frame_context),
        transform_info=_dc(result.transform_info),
    )
