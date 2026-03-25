
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from db.trajectory import get_trajectory, save_trajectory, init_trajectory_table
from app.xarm_status import get_velocity_percent as _get_global_velocity
import dataclasses
import math
import asyncio
import time
from typing import Any
from app.config import (
    IGUS_CONTAINER_IP, IGUS_CONTAINER_PORT,
    XARM_CONTAINER_IP, XARM_CONTAINER_PORT,
    DEPTH_CAMERA_CONTAINER_IP, DEPTH_CAMERA_CONTAINER_PORT,
    XARM_STATUS_CACHE_TTL_SEC, NAV2ADAPTER_BASE_URL,
)
from services.igus_service import IgusMotorClient
from services.xarm_service import XarmManipulatorClient
from app import xarm_status
from services.camera_service import CameraClient
from models.api_types import (
    IgusMoveParams, XarmStatusResponse, IgusStatusResponse,
    XarmMoveWithToolParams, ErrorStatus, XarmMoveWithJointsDictParams,
    IgusMoveResult, XarmMoveResult, XarmJointsDict, XarmMoveWithJointsParams,
)
import logging
import models.xarm_positions as xarm_positions
logger = logging.getLogger(__name__)
from exceptions import RobotError, DeviceReadyError, DeviceConnectionError, DeviceError, SafetyLockoutError
from app.decorator import *
from services.nav2adapter_client import Nav2AdapterClient
from models.base_types import TaskPhase
from app.zone_checks import agv_is_near, arm_tcp_in_job_zone, JOB_ZONE_BOX

# ---------------------------------------------------------------------------
# Safety enforcement — delegates to centralized SafetyKernel
# ---------------------------------------------------------------------------
from safety.safety_kernel import get_safety_kernel as _get_safety_kernel


# ---------------------------------------------------------------------------
# Task-phase tracker (M1 — lightweight FSM for observability)
# ---------------------------------------------------------------------------
_current_phase: TaskPhase = TaskPhase.IDLE
_phase_task_id: str | None = None


def _set_phase(phase: TaskPhase, *, task_id: str | None = None) -> None:
    """Transition to *phase* and log the change."""
    global _current_phase, _phase_task_id
    prev = _current_phase
    _current_phase = phase
    if task_id is not None:
        _phase_task_id = task_id
    if prev != phase:
        logger.info("task_phase: %s → %s (task=%s)", prev.value, phase.value, _phase_task_id)


def get_task_phase() -> dict:
    """Return current phase snapshot (used by /health/details and _TaskManager)."""
    return {"phase": _current_phase.value, "task_id": _phase_task_id}


async def _check_safety_periodic() -> None:
    """Re-check safety lockout during long-running operations (rate-limited).

    Delegates to SafetyKernel which has its own rate limiter.
    """
    await _get_safety_kernel().authorize_motion("periodic")

from routes.decorators import _jsonable as _ns_to_dict  # reuse shared utility

# ---------------------------------------------------------------------------
# Lazy-initialized service clients (created on first access, not at import)
# ---------------------------------------------------------------------------
_lift: IgusMotorClient | None = None
_agv: Nav2AdapterClient | None = None
_manipulator: XarmManipulatorClient | None = None
_depth_camera: CameraClient | None = None
robot_lock = asyncio.Lock()


def _get_lift() -> IgusMotorClient:
    global _lift
    if _lift is None:
        _lift = IgusMotorClient(f"http://{IGUS_CONTAINER_IP}:{IGUS_CONTAINER_PORT}")
    return _lift


def _get_agv() -> Nav2AdapterClient:
    global _agv
    if _agv is None:
        _agv = Nav2AdapterClient(NAV2ADAPTER_BASE_URL)
    return _agv


def _get_manipulator() -> XarmManipulatorClient:
    global _manipulator
    if _manipulator is None:
        url = f"http://{XARM_CONTAINER_IP}:{XARM_CONTAINER_PORT}"
        _manipulator = XarmManipulatorClient(url)
    return _manipulator


def _get_depth_camera() -> CameraClient:
    global _depth_camera
    if _depth_camera is None:
        url = f"http://{DEPTH_CAMERA_CONTAINER_IP}:{DEPTH_CAMERA_CONTAINER_PORT}"
        _depth_camera = CameraClient(url)
    return _depth_camera


class _LazyClient:
    """Descriptor that proxies attribute access to a lazily-created client."""

    def __init__(self, factory):
        self._factory = factory

    def __getattr__(self, name):
        return getattr(self._factory(), name)


# Backward-compatible module-level names (existing code uses ``lift.xxx``, ``agv.xxx``, etc.)
lift = _LazyClient(_get_lift)
agv = _LazyClient(_get_agv)
manipulator = _LazyClient(_get_manipulator)
depth_camera = _LazyClient(_get_depth_camera)


async def cleanup_clients() -> None:
    """Close persistent HTTP sessions for lazy clients (called at shutdown)."""
    global _agv
    if _agv is not None:
        await _agv.aclose()
        _agv = None


async def fault_reset() -> bool:
    try:
        await asyncio.gather(
            lift.fault_reset(),
            manipulator.fault_reset(),
            agv.fault_reset()
        )
        # After clearing faults, re-enable motion on xarm
        await manipulator.enable_motion()
        return True
    except Exception as e:
        raise e


async def igus_move_and_check(pos, velocity):
    logger.info("Moving IGUS lift to position: %s", pos)
    await lift.move(position=pos, velocity_percent=velocity, acceleration_percent=velocity)
    await asyncio.sleep(0)
    pos_resp = await asyncio.wait_for(lift.position(), timeout=5)
    await asyncio.sleep(0)
    igus_pos = pos_resp.get("position", pos_resp) if isinstance(pos_resp, dict) else pos_resp
    logger.info("IGUS at position: %s", igus_pos)
    if abs(igus_pos - pos) < 100:
        return True
    logger.warning("igus_move_and_check: position mismatch — target=%s actual=%s", pos, igus_pos)
    return False

async def _preflight_make_transport_safe(params) -> None:
    v = _get_global_velocity()
    logger.info("preflight: start (transport pose + lift down)")
    # Try to auto-prepare devices if not ready
    try:
        xarm_ok = True
        igus_ok = True
        try:
            xs = await manipulator.status()
            xarm_ok = bool(xs and xs.get("connected") and not xs.get("has_error") and not xs.get("has_err_warn"))
        except Exception:
            xarm_ok = False
        try:
            is_ = await lift.status()
            igus_ok = bool(is_ and is_.get("connected") and is_.get("homed") and not is_.get("error"))
        except Exception:
            igus_ok = False

        if not xarm_ok:
            logger.info("preflight: xarm not ready -> fault_reset")
            try:
                await manipulator.fault_reset()
                await manipulator.enable_motion()
            except Exception as e:
                logger.warning("preflight: xarm fault_reset failed: %s", e)
            try:
                xs = await manipulator.status()
                xarm_ok = bool(
                    xs and xs.get("connected") and not xs.get("has_error") and not xs.get("has_err_warn")
                    and xs.get("motion_enabled", True)
                )
            except Exception:
                xarm_ok = False

        if not igus_ok:
            logger.info("preflight: igus not ready -> fault_reset + reference")
            try:
                await lift.fault_reset()
            except Exception as e:
                logger.warning("preflight: igus fault_reset failed: %s", e)
            try:
                await lift.reference()
            except Exception as e:
                logger.warning("preflight: igus reference failed: %s", e)
            try:
                is_ = await lift.status()
                igus_ok = bool(is_ and is_.get("connected") and is_.get("homed") and not is_.get("error"))
            except Exception:
                igus_ok = False

        if not xarm_ok or not igus_ok:
            missing = []
            if not xarm_ok:
                missing.append("XArm")
            if not igus_ok:
                missing.append("Igus")
            raise DeviceReadyError("Not ready after preflight: " + ", ".join(missing))

        # Ensure manipulator in transport-safe pose
        cur = await manipulator.current_joints_position()
        logger.info("preflight: current joints pose: %s", cur)
        if cur.get('name') != "TRANSPORT_STEP_2":
            p = xarm_positions.get_XarmMoveWithJointsDictParams_for_transport_position()
            await asyncio.wait_for(manipulator.complex_move_with_joints(p), timeout=30)
            logger.info("preflight: manipulator moved to TRANSPORT_STEP_2")

        # Ensure lift down to base
        logger.info("preflight: moving IGUS to base (0)")
        ok = await igus_move_and_check(0, v)
        logger.info("preflight: IGUS to base ok=%s", bool(ok))
    except Exception as e:
        logger.error("preflight: failed: %s", e)
        raise

async def _measure_depth(config: dict) -> float:
    """Measure depth from camera center in mm.

    Returns raw distance in mm.
    Raises RuntimeError when the camera is unavailable or reading is invalid.
    """
    resp = await depth_camera.depth(x_norm=50, y_norm=50)
    raw_mm = resp['depth'] * 1000  # metres → mm
    if raw_mm is None or raw_mm <= 0:
        raise RuntimeError("Depth camera reading is invalid: %s" % raw_mm)
    logger.info("Autotake depth: raw=%.1f mm", raw_mm)
    return raw_mm


async def _verify_vacuum(config: dict) -> tuple[bool, str]:
    """Check gripper PDI: part_present / part_secured flags.

    Returns (is_gripped: bool, feedback: str).
    """
    verify_cfg = config.get("gripperVerify", {}) if isinstance(config, dict) else {}
    sample_count = max(1, int(verify_cfg.get("samples", 3)))
    required_positive = max(1, min(sample_count, int(verify_cfg.get("required", 2))))
    interval_ms = max(50, int(verify_cfg.get("intervalMs", 120)))
    max_errors = max(1, int(verify_cfg.get("maxReadErrors", 3)))

    positives = 0
    pp_count = 0
    last_feedback = "UNKNOWN"
    read_errors = 0
    valid_samples = 0

    max_attempts = sample_count + max_errors
    for idx in range(max_attempts):
        if idx > 0:
            await asyncio.sleep(interval_ms / 1000.0)
        try:
            gs = await manipulator.gripper_status()
        except Exception as read_exc:
            read_errors += 1
            logger.warning("vacuum verify: read error %d/%d: %s", read_errors, max_errors, read_exc)
            if read_errors > max_errors:
                return False, "READ_ERROR"
            continue

        last_feedback = str(gs.get("feedback", "UNKNOWN"))
        part_present = bool(gs.get("part_present", False))
        part_secured = bool(gs.get("part_secured", False))
        vacuum_kpa = gs.get("vacuum_level")

        effective_secured = part_secured or (last_feedback == "PART_GRIPPED")
        effective_present = part_present or (last_feedback in ("PART_GRIPPED", "VACUUM_NO_PART"))

        valid_samples += 1
        if effective_secured:
            positives += 1
        if effective_present:
            pp_count += 1

        logger.debug(
            "vacuum verify sample %d: feedback=%s PP=%s PS=%s effPP=%s effPS=%s vac=%s",
            valid_samples, last_feedback, part_present, part_secured,
            effective_present, effective_secured, vacuum_kpa,
        )
        if valid_samples >= sample_count:
            break

    logger.info(
        "vacuum verify: secured=%d/%d present=%d/%d required=%d feedback=%s errors=%d/%d",
        positives, valid_samples, pp_count, valid_samples,
        required_positive, last_feedback, read_errors, max_errors,
    )

    if positives >= required_positive:
        return True, last_feedback
    # Part present but not secured — weak grip, still accept
    if pp_count >= required_positive:
        logger.warning("vacuum verify: WEAK grip — part_present but not part_secured")
        return True, "PART_PRESENT_NOT_SECURED"
    return False, last_feedback


async def _verify_vacuum_detailed(config: dict, override: dict | None = None) -> dict:
    """Detailed vacuum verification with confidence score output."""
    verify_cfg = config.get("gripperVerify", {}) if isinstance(config, dict) else {}
    if override:
        verify_cfg = {**verify_cfg, **override}

    sample_count = max(1, int(verify_cfg.get("samples", 3)))
    required_positive = max(1, min(sample_count, int(verify_cfg.get("required", 2))))
    interval_ms = max(50, int(verify_cfg.get("intervalMs", 120)))
    max_errors = max(1, int(verify_cfg.get("maxReadErrors", 3)))

    positives = 0
    pp_count = 0
    last_feedback = "UNKNOWN"
    read_errors = 0
    valid_samples = 0
    supported_samples = 0
    max_vacuum = 0.0

    max_attempts = sample_count + max_errors
    for idx in range(max_attempts):
        if idx > 0:
            await asyncio.sleep(interval_ms / 1000.0)
        try:
            gs = await manipulator.gripper_status()
        except Exception as read_exc:
            read_errors += 1
            logger.warning("vacuum verify: read error %d/%d: %s", read_errors, max_errors, read_exc)
            if read_errors > max_errors:
                return {
                    "ok": False,
                    "feedback": "READ_ERROR",
                    "pcs": 0.0,
                    "secured": 0,
                    "present": 0,
                    "valid_samples": valid_samples,
                    "read_errors": read_errors,
                    "required": required_positive,
                }
            continue

        last_feedback, effective_present, effective_secured, vacuum_kpa = _effective_grip_flags(gs)
        sensor_supported = bool(gs.get("sensor_supported", False))
        if sensor_supported:
            supported_samples += 1

        valid_samples += 1
        if effective_secured:
            positives += 1
        if effective_present:
            pp_count += 1
        if isinstance(vacuum_kpa, (int, float)):
            max_vacuum = max(max_vacuum, abs(float(vacuum_kpa)))

        if valid_samples >= sample_count:
            break

    secured_ratio = (positives / valid_samples) if valid_samples else 0.0
    present_ratio = (pp_count / valid_samples) if valid_samples else 0.0
    support_ratio = (supported_samples / valid_samples) if valid_samples else 0.0
    vacuum_ratio = min(1.0, max_vacuum / 60.0)
    pcs = min(1.0, max(0.0, 0.60 * secured_ratio + 0.25 * present_ratio + 0.10 * support_ratio + 0.05 * vacuum_ratio))

    ok = positives >= required_positive or pp_count >= required_positive
    detail_feedback = last_feedback
    if not (positives >= required_positive) and pp_count >= required_positive:
        detail_feedback = "PART_PRESENT_NOT_SECURED"

    logger.info(
        "vacuum verify+pcs: secured=%d/%d present=%d/%d required=%d feedback=%s pcs=%.2f vac_max=%.1f errors=%d/%d",
        positives, valid_samples, pp_count, valid_samples,
        required_positive, detail_feedback, pcs, max_vacuum, read_errors, max_errors,
    )

    return {
        "ok": ok,
        "feedback": detail_feedback,
        "pcs": pcs,
        "secured": positives,
        "present": pp_count,
        "valid_samples": valid_samples,
        "read_errors": read_errors,
        "required": required_positive,
    }


def _effective_grip_flags(gs: dict) -> tuple[str, bool, bool, object]:
    """Normalize raw gripper status into effective grip flags."""
    feedback = str(gs.get("feedback", "UNKNOWN"))
    part_present = bool(gs.get("part_present", False))
    part_secured = bool(gs.get("part_secured", False))
    vacuum_kpa = gs.get("vacuum_level")
    effective_secured = part_secured or (feedback == "PART_GRIPPED")
    effective_present = part_present or (feedback in ("PART_GRIPPED", "VACUUM_NO_PART"))
    return feedback, effective_present, effective_secured, vacuum_kpa


async def _stage3_approach_until_detect(
    config: dict,
    remaining_travel: float,
    velocity: int,
    budget_mm: float | None = None,
) -> tuple[float, bool, str, str]:
    """Approach in micro-steps and stop as soon as grip signal is detected.

    Returns: (moved_z_mm, detected, feedback, reason)
    """
    approach_cfg = config.get("gripperApproach", {}) if isinstance(config, dict) else {}
    step_mm = max(0.5, float(approach_cfg.get("stepMm", 2.0)))
    fine_step_mm = max(0.5, float(approach_cfg.get("fineStepMm", 1.0)))
    detect_consecutive = max(1, int(approach_cfg.get("detectConsecutive", 2)))
    sample_interval_ms = max(30, int(approach_cfg.get("sampleIntervalMs", 80)))
    max_read_errors = max(1, int(approach_cfg.get("maxReadErrors", 3)))
    max_stage3_travel = max(1.0, float(approach_cfg.get("maxStage3TravelMm", 20.0)))

    hard_budget = min(float(remaining_travel), max_stage3_travel)
    stage3_budget = hard_budget if budget_mm is None else max(0.0, min(hard_budget, float(budget_mm)))
    slow_velocity = max(1, velocity // 2)
    moved = 0.0
    consecutive_detect = 0
    read_errors = 0
    last_feedback = "UNKNOWN"
    detected = False

    logger.info(
        "Stage 3 (smart): budget=%.1f step=%.1f fine=%.1f detect=%d interval=%dms vel=%d",
        stage3_budget, step_mm, fine_step_mm, detect_consecutive, sample_interval_ms, slow_velocity,
    )

    while moved < stage3_budget:
        step = step_mm if consecutive_detect == 0 else fine_step_mm
        step = min(step, stage3_budget - moved)
        if step <= 0:
            break

        await manipulator.change_tool_position(XarmMoveWithToolParams(
            x_offset_mm=0, y_offset_mm=0, z_offset_mm=step,
            velocity_percent=slow_velocity, reset_faults=False,
        ))
        moved += step

        await asyncio.sleep(sample_interval_ms / 1000.0)
        try:
            gs = await manipulator.gripper_status()
        except Exception as read_exc:
            read_errors += 1
            logger.warning("Stage 3 read error %d/%d: %s", read_errors, max_read_errors, read_exc)
            if read_errors > max_read_errors:
                return moved, False, "READ_ERROR", "read_error_limit"
            continue

        feedback, effective_present, effective_secured, vacuum_kpa = _effective_grip_flags(gs)
        last_feedback = feedback

        if effective_secured or effective_present:
            consecutive_detect += 1
        else:
            consecutive_detect = 0

        logger.debug(
            "Stage 3 sample: moved=%.1f/%.1f fb=%s effPP=%s effPS=%s consec=%d vac=%s",
            moved, stage3_budget, feedback, effective_present, effective_secured,
            consecutive_detect, vacuum_kpa,
        )

        if consecutive_detect >= detect_consecutive:
            detected = True
            logger.info(
                "Stage 3 detection: moved=%.1f/%.1f feedback=%s (consecutive=%d)",
                moved, stage3_budget, feedback, consecutive_detect,
            )
            break

    reason = "detected" if detected else "stage3_budget_exhausted"
    return moved, detected, last_feedback, reason


async def _run_xy_retry_search(config: dict, velocity: int, total_x: float, total_y: float, total_z: float) -> tuple[bool, float, float, float, str]:
    """Run bounded XY retry probes near contact plane.

    Returns: (detected, add_x, add_y, add_z, feedback)
    """
    xy_cfg = config.get("xySearch", {}) if isinstance(config, dict) else {}
    if not bool(xy_cfg.get("enabled", True)):
        return False, 0.0, 0.0, 0.0, "DISABLED"

    amp = max(0.5, float(xy_cfg.get("amplitudeMm", 2.0)))
    max_probes = max(1, int(xy_cfg.get("maxProbes", 4)))
    z_probe_mm = max(0.5, float(xy_cfg.get("zProbeMm", 1.5)))
    probe_velocity = max(1, int(xy_cfg.get("velocityPercent", max(1, velocity // 3))))

    pattern = [
        (amp, 0.0),
        (-2.0 * amp, 0.0),
        (amp, amp),
        (0.0, -2.0 * amp),
        (0.0, amp),
    ]
    pattern = pattern[:max_probes]

    add_x = 0.0
    add_y = 0.0
    add_z = 0.0
    last_feedback = "UNKNOWN"

    logger.info("Stage 3 XY-search: probes=%d amp=%.1f z_probe=%.1f vel=%d", len(pattern), amp, z_probe_mm, probe_velocity)

    for idx, (dx, dy) in enumerate(pattern, start=1):
        await manipulator.change_tool_position(XarmMoveWithToolParams(
            x_offset_mm=dx, y_offset_mm=dy, z_offset_mm=0,
            velocity_percent=probe_velocity, reset_faults=False,
        ))
        add_x += dx
        add_y += dy

        moved_z, detected, feedback, _ = await _stage3_approach_until_detect(
            config=config,
            remaining_travel=z_probe_mm,
            velocity=probe_velocity,
            budget_mm=z_probe_mm,
        )
        add_z += moved_z
        last_feedback = feedback
        logger.info(
            "Stage 3 XY-search probe %d/%d: dx=%.1f dy=%.1f moved_z=%.1f detected=%s feedback=%s",
            idx, len(pattern), dx, dy, moved_z, detected, feedback,
        )
        if detected:
            return True, add_x, add_y, add_z, last_feedback

    return False, add_x, add_y, add_z, last_feedback


async def _lift_test_gate(config: dict, velocity: int) -> tuple[bool, float, dict]:
    """Lift slightly and re-verify grip stability.

    Returns: (ok, z_offset_applied, verify_details)
    """
    lift_cfg = config.get("liftTest", {}) if isinstance(config, dict) else {}
    if not bool(lift_cfg.get("enabled", True)):
        return True, 0.0, {"ok": True, "feedback": "LIFT_TEST_DISABLED", "pcs": 1.0}

    lift_mm = max(0.0, float(lift_cfg.get("liftMm", 8.0)))
    hold_ms = max(50, int(lift_cfg.get("holdMs", 220)))
    lift_velocity = max(1, int(lift_cfg.get("velocityPercent", max(1, velocity // 2))))
    verify_override = {
        "samples": max(1, int(lift_cfg.get("samples", 2))),
        "required": max(1, int(lift_cfg.get("required", 2))),
        "intervalMs": max(50, int(lift_cfg.get("intervalMs", 100))),
        "maxReadErrors": max(1, int(lift_cfg.get("maxReadErrors", 2))),
    }

    if lift_mm > 0:
        await manipulator.change_tool_position(XarmMoveWithToolParams(
            x_offset_mm=0, y_offset_mm=0, z_offset_mm=-lift_mm,
            velocity_percent=lift_velocity, reset_faults=False,
        ))
        await asyncio.sleep(hold_ms / 1000.0)

    details = await _verify_vacuum_detailed(config, override=verify_override)
    return bool(details.get("ok", False)), (-lift_mm if lift_mm > 0 else 0.0), details


@guarded_async_call(robot_lock)
async def autotake(velocity: int) -> bool:
    """3-stage autotake: approach → refine → contact.

    Stage 1 (far):   distance > 150 mm → move Z only (no X/Y offsets), keep camera on target.
    Stage 2 (mid):   150..20 mm → re-measure depth (glare protection), apply X/Y offsets from trajectory.
    Stage 3 (close): last 20 mm → half speed, blind approach, then vacuum verification.
    """
    _set_phase(TaskPhase.EXECUTE)
    # ── Pre-condition: AGV must be stationary before arm moves ──────────
    await _assert_agv_stopped()

    # ── Thresholds ──────────────────────────────────────────────────────
    STAGE2_THRESHOLD_MM = 250.0   # switch from stage 1 → stage 2
    STAGE3_THRESHOLD_MM = 20.0    # switch from stage 2 → stage 3
    MAX_DISTANCE_MM = 1000.0
    MIN_DISTANCE_MM = 30.0

    try:
        await manipulator.enable_motion()
        init_trajectory_table()
        config = get_trajectory()

        # ── Initial depth measurement ───────────────────────────────────
        try:
            distance = await _measure_depth(config)
        except Exception as e:
            logger.warning("Depth camera unavailable: %s", e)
            return False

        if distance > MAX_DISTANCE_MM:
            raise RuntimeError("Distance %.1f mm exceeds %d mm limit" % (distance, MAX_DISTANCE_MM))
        if distance < MIN_DISTANCE_MM:
            raise RuntimeError("Distance %.1f mm below %d mm limit" % (distance, MIN_DISTANCE_MM))

        # Trajectory offsets from DB
        base_cfg = config.get('baseMove', {})
        base_active = base_cfg.get('active', False)
        offset_x = float(base_cfg.get('posX', 0))
        offset_y = float(base_cfg.get('posY', 0))
        offset_z = float(base_cfg.get('posZ', 0))  # additional Z shift (geometry compensation)

        # Accumulate all applied moves for precise return
        total_x = 0.0
        total_y = 0.0
        total_z = 0.0
        gripper_on = False

        # ── Prefix move (unchanged) ────────────────────────────────────
        if config.get('prefix', {}).get('active'):
            pfx = config['prefix']
            await manipulator.change_tool_position(XarmMoveWithToolParams(
                x_offset_mm=pfx['posX'], y_offset_mm=pfx['posY'], z_offset_mm=pfx['posZ'],
                velocity_percent=velocity, reset_faults=False,
            ))

        if not base_active:
            logger.info("Autotake: baseMove not active — skipping approach stages")
        else:
            # `distance` = calibrated camera depth (physical distance from camera to object)
            # `offset_z` = geometry compensation (e.g. camera recessed behind suction cups)
            # Total Z to travel = distance + offset_z
            # Thresholds are in terms of CAMERA distance to object (physical), not travel distance
            total_travel = distance + offset_z
            logger.info(
                "Autotake plan: cam_distance=%.1f offset_z=%.1f total_travel=%.1f offset_x=%.1f offset_y=%.1f",
                distance, offset_z, total_travel, offset_x, offset_y,
            )

            # ── STAGE 1 (far): Z-only approach keeping camera aimed at object ─
            # Stop when camera distance to object ≈ STAGE2_THRESHOLD
            if distance > STAGE2_THRESHOLD_MM:
                stage1_z = distance - STAGE2_THRESHOLD_MM
                logger.info("Stage 1 (far): moving Z=%.1f mm (cam_distance %.1f → %.1f)",
                            stage1_z, distance, STAGE2_THRESHOLD_MM)
                await manipulator.change_tool_position(XarmMoveWithToolParams(
                    x_offset_mm=0, y_offset_mm=0, z_offset_mm=stage1_z,
                    velocity_percent=velocity, reset_faults=False,
                ))
                total_z += stage1_z
                distance -= stage1_z  # update estimated camera distance
                logger.info("Stage 1 done: est. cam_distance=%.1f mm", distance)

            # ── STAGE 2 (mid): re-measure depth, apply X/Y offsets ────────
            try:
                new_depth = await _measure_depth(config)
                if new_depth >= MIN_DISTANCE_MM:
                    logger.info("Stage 2: fresh depth=%.1f (was %.1f)", new_depth, distance)
                    distance = new_depth
                else:
                    logger.warning("Stage 2: bad reading %.1f (< %.1f) — keeping old estimate %.1f",
                                   new_depth, MIN_DISTANCE_MM, distance)
            except Exception as depth_exc:
                logger.warning("Stage 2: re-measurement failed (%s), using old estimate %.1f", depth_exc, distance)

            # ── GRASP ANALYSIS (between depth re-measure and move) ────────
            grasp_cfg = config.get("graspAnalysis", {}) if isinstance(config, dict) else {}
            grasp_enabled = bool(grasp_cfg.get("enabled", False))
            roll_corr = 0.0
            pitch_corr = 0.0
            yaw_corr = 0.0
            xy_corr_x = 0.0
            xy_corr_y = 0.0
            grasp_analysis_ok = False

            if grasp_enabled:
                try:
                    # Capture frame and analyse on the same snapshot
                    cap = await manipulator.capture_depth_frame()
                    frame_id = cap.get("frame_id")
                    # Forward algorithm params from trajectory config
                    algo_kwargs = {}
                    if "yawSearchStepDeg" in grasp_cfg:
                        algo_kwargs["yaw_search_step_deg"] = float(grasp_cfg["yawSearchStepDeg"])
                    if "collisionEnvelopeMm" in grasp_cfg:
                        algo_kwargs["collision_envelope_mm"] = float(grasp_cfg["collisionEnvelopeMm"])
                    if "sealPerimeterPoints" in grasp_cfg:
                        algo_kwargs["seal_perimeter_points"] = int(grasp_cfg["sealPerimeterPoints"])
                    if "cupGridStepMm" in grasp_cfg:
                        algo_kwargs["cup_grid_step_mm"] = float(grasp_cfg["cupGridStepMm"])
                    ga = await manipulator.analyze_grasp(
                        frame_id=frame_id, x_norm=50.0, y_norm=50.0, **algo_kwargs,
                    )

                    ga_status = ga.get("status", "FAILED") if ga else "FAILED"
                    if ga_status in ("OK", "DEGRADED"):
                        roll_corr = float(ga.get("roll_correction_deg", 0.0))
                        pitch_corr = float(ga.get("pitch_correction_deg", 0.0))
                        yaw_corr = float(ga.get("yaw_correction_deg", 0.0))
                        xy_mm = ga.get("xy_correction_mm", [0.0, 0.0])
                        xy_corr_x = float(xy_mm[0]) if len(xy_mm) > 0 else 0.0
                        xy_corr_y = float(xy_mm[1]) if len(xy_mm) > 1 else 0.0
                        confidence = float(ga.get("confidence", 0.0))
                        approach_clear = bool(ga.get("approach_clear", True))

                        # Per-trajectory RPY clamping
                        max_roll = float(grasp_cfg.get("maxRollDeg", 8.0))
                        max_pitch = float(grasp_cfg.get("maxPitchDeg", 8.0))
                        max_yaw = float(grasp_cfg.get("maxYawDeg", 30.0))
                        roll_corr = max(-max_roll, min(max_roll, roll_corr))
                        pitch_corr = max(-max_pitch, min(max_pitch, pitch_corr))
                        yaw_corr = max(-max_yaw, min(max_yaw, yaw_corr))

                        # Seal score threshold enforcement
                        dcr = ga.get("dual_cup_result") or {}
                        combined_seal = float(dcr.get("combined_seal", 0.0))
                        activation_mode = dcr.get("activation_mode", "BOTH")
                        min_seal = float(grasp_cfg.get("minSealScore", 0.70))
                        min_single_seal = float(grasp_cfg.get("minSingleCupSeal", 0.50))
                        allow_single = bool(grasp_cfg.get("allowSingleCupPick", True))

                        seal_ok = True
                        if combined_seal < min_seal:
                            logger.warning(
                                "Grasp analysis: combined_seal %.2f < threshold %.2f — rejecting corrections",
                                combined_seal, min_seal,
                            )
                            seal_ok = False
                        if activation_mode in ("CUP_A_ONLY", "CUP_B_ONLY"):
                            if not allow_single:
                                logger.warning(
                                    "Grasp analysis: single-cup mode %s but allowSingleCupPick=False — rejecting",
                                    activation_mode,
                                )
                                seal_ok = False
                            else:
                                # Check the active cup meets single-cup threshold
                                cup_key = "cup_a" if activation_mode == "CUP_A_ONLY" else "cup_b"
                                active_seal = float(dcr.get(cup_key, {}).get("seal_score", 0.0))
                                if active_seal < min_single_seal:
                                    logger.warning(
                                        "Grasp analysis: active cup seal %.2f < single-cup threshold %.2f — rejecting",
                                        active_seal, min_single_seal,
                                    )
                                    seal_ok = False

                        if seal_ok:
                            grasp_analysis_ok = True
                        else:
                            # Zero out corrections — fall back to base offsets
                            roll_corr = pitch_corr = yaw_corr = 0.0
                            xy_corr_x = xy_corr_y = 0.0

                        logger.info(
                            "Grasp analysis: status=%s conf=%.3f seal=%.2f mode=%s roll=%.1f pitch=%.1f yaw=%.1f xy=(%.1f,%.1f) clear=%s accepted=%s",
                            ga_status, confidence, combined_seal, activation_mode,
                            roll_corr, pitch_corr, yaw_corr, xy_corr_x, xy_corr_y, approach_clear, grasp_analysis_ok,
                        )
                        if ga_status == "DEGRADED":
                            logger.warning("Grasp analysis degraded: %s", ga.get("reason_codes", []))

                        # Collision abort check
                        abort_on_collision = bool(grasp_cfg.get("abortOnCollision", False))
                        if not approach_clear and abort_on_collision:
                            logger.warning("Grasp analysis: corridor BLOCKED and abortOnCollision=True — aborting")
                            return False
                    else:
                        logger.warning("Grasp analysis FAILED: %s — using base offsets only", ga.get("reason_codes", []))
                except Exception as ga_exc:
                    logger.warning("Grasp analysis unavailable: %s — falling back to base offsets", ga_exc)

            # Apply grasp analysis corrections additively to base offsets
            effective_offset_x = offset_x + xy_corr_x
            effective_offset_y = offset_y + xy_corr_y

            # Now calculate remaining travel = camera distance + offset_z
            remaining_travel = distance + offset_z
            logger.info("Stage 2: cam_distance=%.1f + offset_z=%.1f → remaining_travel=%.1f",
                        distance, offset_z, remaining_travel)

            # Move to within STAGE3_THRESHOLD of travel distance, applying X/Y offsets
            if remaining_travel > STAGE3_THRESHOLD_MM:
                stage2_z = remaining_travel - STAGE3_THRESHOLD_MM
                # Optional early VAC ON in Stage 2 to build vacuum before final contact
                if config.get('gripper', {}).get('active'):
                    approach_cfg = config.get("gripperApproach", {}) if isinstance(config, dict) else {}
                    enable_stage2 = bool(approach_cfg.get("enableAtStage2", True))
                    if enable_stage2 and not gripper_on:
                        try:
                            await manipulator.gripper_take()
                            gripper_on = True
                            logger.info(
                                "Gripper activated early in Stage 2 (VAC ON), remaining_travel=%.1f",
                                remaining_travel,
                            )
                        except Exception as e:
                            logger.warning("Stage 2 early gripper activation failed: %s", e)

                # Build move params with optional RPY corrections from grasp analysis
                move_kwargs = dict(
                    x_offset_mm=effective_offset_x,
                    y_offset_mm=effective_offset_y,
                    z_offset_mm=stage2_z,
                    velocity_percent=velocity,
                    reset_faults=False,
                )
                if grasp_analysis_ok:
                    move_kwargs["roll_offset_deg"] = roll_corr
                    move_kwargs["pitch_offset_deg"] = pitch_corr
                    move_kwargs["yaw_offset_deg"] = yaw_corr

                logger.info(
                    "Stage 2 (mid): moving x=%.1f y=%.1f z=%.1f roll=%.1f pitch=%.1f yaw=%.1f (remaining_travel %.1f → %.1f)",
                    effective_offset_x, effective_offset_y, stage2_z, roll_corr, pitch_corr, yaw_corr,
                    remaining_travel, STAGE3_THRESHOLD_MM,
                )
                await manipulator.change_tool_position(XarmMoveWithToolParams(**move_kwargs))
                total_x += effective_offset_x
                total_y += effective_offset_y
                total_z += stage2_z
                remaining_travel = STAGE3_THRESHOLD_MM
            else:
                # Already close enough — just apply X/Y offsets
                logger.info("Stage 2: already within %.1f mm — applying X/Y offsets only", STAGE3_THRESHOLD_MM)
                if effective_offset_x != 0 or effective_offset_y != 0:
                    move_kwargs = dict(
                        x_offset_mm=effective_offset_x,
                        y_offset_mm=effective_offset_y,
                        z_offset_mm=0,
                        velocity_percent=velocity,
                        reset_faults=False,
                    )
                    if grasp_analysis_ok:
                        move_kwargs["roll_offset_deg"] = roll_corr
                        move_kwargs["pitch_offset_deg"] = pitch_corr
                        move_kwargs["yaw_offset_deg"] = yaw_corr
                    await manipulator.change_tool_position(XarmMoveWithToolParams(**move_kwargs))
                    total_x += effective_offset_x
                    total_y += effective_offset_y

            logger.info("Stage 2 done: remaining_travel=%.1f mm", remaining_travel)

            # ── Gripper ON (between stage 2 and 3) ────────────────────────
            # Keep backward-compat path if early Stage 2 activation is disabled.
            if config.get('gripper', {}).get('active') and not gripper_on:
                try:
                    await manipulator.gripper_take()
                    gripper_on = True
                    logger.info("Gripper activated (VAC ON) at %.1f mm travel left", remaining_travel)
                except Exception as e:
                    logger.warning("Gripper activation failed: %s — continuing approach", e)

            # ── STAGE 3 (close): segmented approach with early stop ───────
            approach_cfg = config.get("gripperApproach", {}) if isinstance(config, dict) else {}
            max_stage3 = max(1.0, float(approach_cfg.get("maxStage3TravelMm", 20.0)))
            primary_ratio = min(0.95, max(0.40, float(approach_cfg.get("primaryBudgetRatio", 0.70))))
            primary_budget = max_stage3 * primary_ratio

            stage3_moved, detected, stage3_feedback, stop_reason = await _stage3_approach_until_detect(
                config=config,
                remaining_travel=remaining_travel,
                velocity=velocity,
                budget_mm=primary_budget,
            )
            total_z += stage3_moved
            logger.info(
                "Stage 3 done: moved_z=%.1f detected=%s reason=%s feedback=%s total: x=%.1f y=%.1f z=%.1f",
                stage3_moved, detected, stop_reason, stage3_feedback, total_x, total_y, total_z,
            )

            if not detected:
                retry_detected, add_x, add_y, add_z, retry_feedback = await _run_xy_retry_search(
                    config=config,
                    velocity=velocity,
                    total_x=total_x,
                    total_y=total_y,
                    total_z=total_z,
                )
                total_x += add_x
                total_y += add_y
                total_z += add_z
                detected = retry_detected
                if retry_detected:
                    stage3_feedback = retry_feedback
                    logger.info(
                        "Stage 3 XY-search detected grip: add_x=%.1f add_y=%.1f add_z=%.1f feedback=%s",
                        add_x, add_y, add_z, stage3_feedback,
                    )

            if detected:
                settle_mm = max(0.0, float(approach_cfg.get("microSettleMm", 1.5)))
                residual_budget = max(0.0, max_stage3 - stage3_moved)
                micro_settle = min(settle_mm, residual_budget)
                if micro_settle > 0:
                    slow_velocity = max(1, velocity // 2)
                    await manipulator.change_tool_position(XarmMoveWithToolParams(
                        x_offset_mm=0, y_offset_mm=0, z_offset_mm=micro_settle,
                        velocity_percent=slow_velocity, reset_faults=False,
                    ))
                    total_z += micro_settle
                    logger.info("Stage 3 micro-settle: z=%.1f (after detection)", micro_settle)

            # ── Vacuum verification after contact ─────────────────────────
            if config.get('gripper', {}).get('active'):
                await asyncio.sleep(0.2)  # let vacuum settle
                verify = await _verify_vacuum_detailed(config)
                grip_ok = bool(verify.get("ok", False))
                feedback = str(verify.get("feedback", "UNKNOWN"))
                pcs = float(verify.get("pcs", 0.0))

                if not grip_ok:
                    second_cfg = config.get("gripperVerifySecondChance", {}) if isinstance(config, dict) else {}
                    if bool(second_cfg.get("enabled", True)) and detected:
                        delay_ms = max(50, int(second_cfg.get("delayMs", 180)))
                        min_pcs = max(0.0, min(1.0, float(second_cfg.get("minPcs", 0.30))))
                        logger.warning(
                            "Vacuum first check failed after detection (feedback=%s pcs=%.2f) — second chance in %dms",
                            feedback, pcs, delay_ms,
                        )
                        await asyncio.sleep(delay_ms / 1000.0)
                        recheck = await _verify_vacuum_detailed(
                            config,
                            override={
                                "samples": max(1, int(second_cfg.get("samples", 2))),
                                "required": max(1, int(second_cfg.get("required", 1))),
                                "intervalMs": max(50, int(second_cfg.get("intervalMs", 90))),
                                "maxReadErrors": max(1, int(second_cfg.get("maxReadErrors", 2))),
                            },
                        )
                        re_ok = bool(recheck.get("ok", False))
                        re_feedback = str(recheck.get("feedback", "UNKNOWN"))
                        re_pcs = float(recheck.get("pcs", 0.0))
                        if re_ok or re_pcs >= min_pcs:
                            grip_ok = True
                            feedback = re_feedback
                            pcs = re_pcs
                            logger.info(
                                "Vacuum second chance accepted (feedback=%s pcs=%.2f)",
                                feedback, pcs,
                            )

                if not grip_ok:
                    logger.error(
                        "Autotake vacuum check FAILED (feedback=%s pcs=%.2f) — reversing and aborting", feedback, pcs,
                    )
                    # Release vacuum
                    try:
                        await manipulator.gripper_drop()
                    except Exception:
                        pass
                    # Reverse all accumulated moves
                    try:
                        await manipulator.change_tool_position(XarmMoveWithToolParams(
                            x_offset_mm=-total_x, y_offset_mm=-total_y, z_offset_mm=-total_z,
                            velocity_percent=velocity, reset_faults=True,
                        ))
                    except Exception as rev_exc:
                        logger.warning("Autotake reverse after failed vacuum: %s", rev_exc)
                    raise RuntimeError(
                        "Vacuum grip not confirmed after contact (feedback=%s) — reversed" % feedback
                    )
                logger.info("Vacuum check OK (feedback=%s pcs=%.2f) — grip confirmed", feedback, pcs)

                # ── Lift-test gate (post-grip stability check) ─────────────────
                lift_ok, lift_z, lift_details = await _lift_test_gate(config, velocity)
                total_z += lift_z
                if not lift_ok:
                    logger.error(
                        "Lift-test FAILED (feedback=%s pcs=%.2f) — reversing and aborting",
                        lift_details.get("feedback"), float(lift_details.get("pcs", 0.0)),
                    )
                    try:
                        await manipulator.gripper_drop()
                    except Exception:
                        pass
                    try:
                        await manipulator.change_tool_position(XarmMoveWithToolParams(
                            x_offset_mm=-total_x, y_offset_mm=-total_y, z_offset_mm=-total_z,
                            velocity_percent=velocity, reset_faults=True,
                        ))
                    except Exception as rev_exc:
                        logger.warning("Autotake reverse after failed lift-test: %s", rev_exc)
                    raise RuntimeError(
                        "Lift-test failed after grip confirm (feedback=%s)" % lift_details.get("feedback")
                    )
                logger.info(
                    "Lift-test OK (feedback=%s pcs=%.2f)",
                    lift_details.get("feedback"), float(lift_details.get("pcs", 0.0)),
                )

        # ── Postfix (unchanged) ─────────────────────────────────────────
        if config.get('postfix', {}).get('active'):
            pfx = config['postfix']
            await manipulator.change_tool_position(XarmMoveWithToolParams(
                x_offset_mm=pfx['posX'], y_offset_mm=pfx['posY'], z_offset_mm=pfx['posZ'],
                velocity_percent=velocity, reset_faults=False,
            ))

        # ── Return move ─────────────────────────────────────────────────
        if config.get('return', {}).get('active') and (total_x or total_y or total_z):
            logger.info("Return: reversing x=%.1f y=%.1f z=%.1f", -total_x, -total_y, -total_z)
            await manipulator.change_tool_position(XarmMoveWithToolParams(
                x_offset_mm=-total_x, y_offset_mm=-total_y, z_offset_mm=-total_z,
                velocity_percent=velocity, reset_faults=False,
            ))

        # ── Basket drop ──────────────────────────────────────────────
        basket_cfg = config.get('basket', {})
        if basket_cfg.get('active'):
            box_num = int(basket_cfg.get('boxNumber', 1))
            depth_mm = float(basket_cfg.get('depthMm', 80))
            logger.info("Basket: moving to box %d, depth=%.1f mm", box_num, depth_mm)
            await igus_move_and_check(30000, velocity / 2)
            # Joint-space move to approach position (STEP_2)
            approach_params = xarm_positions.get_XarmMoveWithJointsDictParams_direct_to_box(box_num)
            approach_params.velocity_percent = velocity
            await asyncio.wait_for(manipulator.complex_move_with_joints(approach_params), timeout=60)
            # Descend straight into box (tool space)
            await manipulator.change_tool_position(XarmMoveWithToolParams(
                x_offset_mm=0, y_offset_mm=0, z_offset_mm=depth_mm,
                velocity_percent=velocity, reset_faults=False,
            ))
            if basket_cfg.get('drop'):
                logger.info("Basket: dropping gripper")
                await manipulator.gripper_drop()
            # Ascend straight back out (tool space)
            logger.info("Basket: ascending %.1f mm", depth_mm)
            await manipulator.change_tool_position(XarmMoveWithToolParams(
                x_offset_mm=0, y_offset_mm=0, z_offset_mm=-depth_mm,
                velocity_percent=velocity, reset_faults=False,
            ))

        logger.info("Autotake completed successfully")
        return True
    except asyncio.CancelledError:
        logger.warning("Autotake cancelled → stopping all devices")
        await _stop_all_devices()
        raise
    except Exception as e:
        logger.error("Autotake failed: %s", e)
        raise DeviceConnectionError(f"Autotake operation failed: {e}") from e
    finally:
        _set_phase(TaskPhase.IDLE)

@guarded_async_call(robot_lock)
async def set_ready() -> bool:
    await fault_reset()
    await lift.reference()
    status = await get_robot_system_status()
    ready = bool(status.get("ready"))
    message = status.get("message", "")
    logger.info("Robot ready: %s", ready)
    if not ready:
        raise DeviceReadyError(f"Not ready: {message}")
    return True

async def _move_robot_to_box(box_num: int, velocity: int) -> bool:
    """Shared implementation for move_robot_to_box_1/2 with safety checks."""
    await _check_safety_periodic()
    await _assert_agv_stopped()

    v = _get_global_velocity()

    # Preflight: ensure devices are ready
    try:
        xs = await manipulator.status()
        if not (xs and xs.get("connected") and not xs.get("has_error")):
            await manipulator.fault_reset()
            await manipulator.enable_motion()
    except Exception as e:
        logger.warning("move_to_box_%d: xarm preflight failed: %s", box_num, e)

    if await igus_move_and_check(30000, v / 2):
        params = xarm_positions.get_XarmMoveWithJointsDictParams_with_box_num(box_num)
        params.velocity_percent = v
        await asyncio.wait_for(manipulator.complex_move_with_joints(params), timeout=60)
        return True
    return False


@guarded_async_call(robot_lock)
async def move_robot_to_box_1(velocity: int) -> bool:
    return await _move_robot_to_box(1, velocity)


@guarded_async_call(robot_lock)
async def move_robot_to_box_2(velocity: int) -> bool:
    return await _move_robot_to_box(2, velocity)

@guarded_async_call(robot_lock)
async def move_to_transport_position(velocity: int) -> bool:
    await _check_safety_periodic()
    await _assert_agv_stopped()

    v = _get_global_velocity()
    if not await igus_move_and_check(20000, v):
        return False
    current_pose = await manipulator.current_joints_position()
    if current_pose['name'] != "TRANSPORT_STEP_2":
        params = xarm_positions.get_XarmMoveWithJointsDictParams_for_transport_position()
        await asyncio.wait_for(manipulator.complex_move_with_joints(params), timeout=15)
    if not await igus_move_and_check(0, v):
        return False
    return True

# ---------------------------------------------------------------------------
# Navigation context — carries state between phase functions
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class _NavigationContext:
    """Parameters and intermediate state for move_robot_to_product phases."""
    target_x: float
    target_y: float
    target_theta: float
    target_map: Any  # int | str | None
    lift_units: int
    xarm_joints: Any  # SimpleNamespace with .joints or None
    velocity: float
    same_shelf: bool = False

    @classmethod
    def from_params(cls, params) -> "_NavigationContext":
        location = getattr(params, 'location', None)
        lift_cm = float(getattr(params, 'lift_position_cm', 0) or 0)
        return cls(
            target_x=float(getattr(location, 'x_m', 0)) if location else 0.0,
            target_y=float(getattr(location, 'y_m', 0)) if location else 0.0,
            target_theta=float(getattr(location, 'theta_deg', 0.0) or 0.0) if location else 0.0,
            target_map=getattr(location, 'map_id', None) if location else None,
            lift_units=int(lift_cm * 1000),
            xarm_joints=getattr(params, 'xarm_joints', None),
            velocity=_get_global_velocity(),
        )


LIFT_TRANSPORT_MAX   = 20_000  # encoder units — транспортная высота лифта
AGV_POLL_INTERVAL_S  = 1.0    # секунды между опросами позиции AGV
AGV_ARRIVAL_TOL_M    = 0.10   # метры — допуск "приехал" (обычная навигация)
AGV_MICROSTEP_TOL_M  = 0.03   # метры — допуск для микрошага (3 см)

# Micro-step teleop for short backward corrections (avoids double 180° turn)
AGV_MICROSTEP_DIST_M = 1.00   # max дистанция для микрошага назад, иначе go_to_pose
AGV_MICROSTEP_MAX_SPD= 0.08   # m/s — максимальная скорость
AGV_MICROSTEP_MIN_SPD= 0.03   # m/s — минимальная скорость
AGV_MICROSTEP_K      = 0.6    # пропорциональный коэффициент (speed = K × dist)
AGV_MICROSTEP_DUR    = 0.20   # s — длительность каждого пульса
AGV_MICROSTEP_MAX    = 60     # максимум пульсов (~12 s timeout)


# ---------------------------------------------------------------------------
# Coordination gates: AGV ↔ Arm mutual exclusion
# ---------------------------------------------------------------------------
_TRANSPORT_SAFE_POSITIONS = frozenset({"TRANSPORT_STEP_2", "JOB_POSE"})
_AGV_VELOCITY_THRESHOLD = 0.05  # m/s — sensor noise after micro-step can read ~0.03


async def _assert_arm_stowed_for_transport() -> None:
    """Raise DeviceReadyError if the arm is not in a transport-safe position."""
    cur = await manipulator.current_joints_position()
    name = cur.get("name", "") if isinstance(cur, dict) else ""
    if name not in _TRANSPORT_SAFE_POSITIONS:
        # Named position not matched — fall back to TCP bounding-box check.
        # xArm tolerance for name matching can be tighter than the actual
        # safe zone, so accept any TCP inside the job-zone box.
        if await arm_tcp_in_job_zone(manipulator):
            logger.info("_assert_arm_stowed_for_transport: name=%r not in safe set, but TCP in job-zone — OK", name)
            return
        raise DeviceReadyError(f"Arm not stowed for transport (current position: {name})")


async def _assert_agv_stopped(*, retries: int = 20, interval: float = 0.5) -> None:
    """Wait for AGV to come to a full stop after navigation.

    AGV may still be decelerating when _wait_agv_arrival returns (position
    within tolerance but velocity > 0).  Poll up to *retries* times with
    *interval* seconds between checks before raising.

    IMPORTANT — the caller (move_robot_to_product) catches DeviceReadyError
    from this function as a non-fatal warning.  After micro-step navigation
    the AGV can report residual velocity (~0.03 m/s) for 10+ seconds due to
    sensor noise or drive_mode disable lag.  Do NOT make this fatal — it
    would block POSITIONING (lift + arm) even though the AGV is physically
    stationary.  Threshold is 0.01 m/s; micro-step residual is ~0.03 m/s.
    """
    for attempt in range(retries):
        st = await agv.status()
        vel = st.get("velocity", {}) if isinstance(st, dict) else {}
        vx = abs(float(vel.get("vx_m_s", 0) or 0))
        vy = abs(float(vel.get("vy_m_s", 0) or 0))
        if vx <= _AGV_VELOCITY_THRESHOLD and vy <= _AGV_VELOCITY_THRESHOLD:
            return
        logger.debug("_assert_agv_stopped: still moving vx=%.3f vy=%.3f (attempt %d/%d)",
                     vx, vy, attempt + 1, retries)
        await asyncio.sleep(interval)
    raise DeviceReadyError(f"AGV still moving after {retries} checks (vx={vx:.3f}, vy={vy:.3f})")


async def _preflight_for_navigation(caller: str) -> float:
    """Shared preflight: recover devices, arm→JOB_POSE, lift down.

    Used by move_robot_to_product (when not same_shelf) and go_to_charging_station.
    Returns the global velocity for downstream phases.
    """
    v = _get_global_velocity()

    # Deactivate charging stations so AGV doesn't return to dock after navigation.
    # Retry up to 3 times — if stations stay active, Symovo may autonomously
    # drive to the charger mid-task with arm/lift deployed.
    _CHARGING_DEACTIVATE_DELAYS = (3.0, 8.0, 13.0)
    for _attempt, _delay in enumerate(_CHARGING_DEACTIVATE_DELAYS, 1):
        try:
            res = await agv.disable_all_charging_stations()
            logger.info("%s: charging stations deactivated (attempt %d): %s", caller, _attempt, res)
            if res.get("all_inactive"):
                break
            logger.warning("%s: charging stations still active after attempt %d: %s", caller, _attempt, res)
        except Exception as e:
            logger.warning("%s: charging station deactivation failed (attempt %d): %s", caller, _attempt, e)
        if _attempt < len(_CHARGING_DEACTIVATE_DELAYS):
            await asyncio.sleep(_delay)
    else:
        raise DeviceReadyError(
            f"{caller}: failed to deactivate charging stations after {len(_CHARGING_DEACTIVATE_DELAYS)} attempts — aborting to prevent unsafe AGV motion"
        )

    # Auto-recover devices — strict: abort if any device fails
    try:
        await manipulator.fault_reset()
        await manipulator.enable_motion()
        logger.info("%s: xArm enabled", caller)
    except Exception as e:
        raise DeviceReadyError(f"Preflight failed: xArm enable error: {e}") from e
    try:
        await lift.fault_reset()
        logger.info("%s: lift fault_reset ok", caller)
    except Exception as e:
        raise DeviceReadyError(f"Preflight failed: lift fault_reset error: {e}") from e

    # Arm → JOB_POSE if not already there
    cur = await manipulator.current_joints_position()
    if cur.get('name') != 'JOB_POSE':
        logger.info("%s: PREFLIGHT → JOB_POSE", caller)
        await _move_to_job_pose(v)

    # Lift down to 0 for transport
    pos_resp = await lift.position()
    lift_pos = pos_resp.get("position", 0) if isinstance(pos_resp, dict) else float(pos_resp or 0)
    if lift_pos > 0:
        logger.info("%s: PREFLIGHT lift down (%s) → 0", caller, lift_pos)
        await igus_move_and_check(0, v)

    return v


async def _move_to_job_pose(velocity: float) -> None:
    """Перемещает манипулятор в JOB_POSE.

    Если TCP уже внутри job-zone — едет напрямую (TRANSPORT_STEP_1 пропускается).
    Иначе: TRANSPORT_STEP_1 → JOB_POSE.
    """
    if not await arm_tcp_in_job_zone(manipulator):
        logger.info("_move_to_job_pose: TCP outside job-zone → via TRANSPORT_STEP_1")
        p1 = xarm_positions.get_XarmMoveWithJointsDictParams_for_transport_step_1()
        await asyncio.wait_for(manipulator.complex_move_with_joints(p1), timeout=30)
    else:
        logger.info("_move_to_job_pose: TCP in job-zone → skip TRANSPORT_STEP_1")
    p2 = xarm_positions.get_XarmMoveWithJointsDictParams_for_job_pose()
    await asyncio.wait_for(manipulator.complex_move_with_joints(p2), timeout=30)


async def test_job_zone_corners() -> dict:
    """Объезжает 8 углов JOB_ZONE_BOX для проверки достижимости.

    Предварительно перемещает манипулятор в JOB_POSE, читает текущую
    ориентацию TCP (roll/pitch/yaw) и использует её для всех углов.
    Возвращает dict с полем 'corners' (per-corner ok/error) и 'all_ok'.
    """
    await _move_to_job_pose(20.0)
    tcp = await manipulator.tcp_position()
    roll = float(tcp["roll"])
    pitch = float(tcp["pitch"])
    yaw = float(tcp["yaw"])
    box = JOB_ZONE_BOX
    corners = [
        (x, y, z)
        for x in box["x"]
        for y in box["y"]
        for z in box["z"]
    ]
    results = []
    for cx, cy, cz in corners:
        try:
            await asyncio.wait_for(
                manipulator.set_tcp_position(
                    {"x": cx, "y": cy, "z": cz, "roll": roll, "pitch": pitch, "yaw": yaw, "velocity_percent": 15.0}
                ),
                timeout=30,
            )
            results.append({"corner": [cx, cy, cz], "ok": True})
        except Exception as e:
            results.append({"corner": [cx, cy, cz], "ok": False, "error": str(e)})
    all_ok = all(r["ok"] for r in results)
    return {"all_ok": all_ok, "corners": results}


_AGV_NAVIGATION_HARD_TIMEOUT_S = 300.0  # 5 min — abort if AGV hasn't arrived


async def _wait_agv_arrival(target_x: float, target_y: float) -> None:
    """Wait for AGV to arrive at target position.

    Uses nav2adapter's /status/navigation as primary signal (arrived/error),
    with pose-distance as fallback.  Hard timeout of 5 minutes.

    SafetyLockoutError during navigation is non-fatal: the safety relay
    can flicker (2-10s cycles) during normal operation.  AGV hardware
    stops independently — killing the task here would prevent POSITIONING.

    Expected outcomes:
      - nav status "arrived" OR pose within tolerance → return (outcome #4)
      - nav status "error" → raise DeviceError (outcome #2)
      - hard timeout → raise DeviceConnectionError (outcome #2)
      - AGV offline → raise DeviceConnectionError (outcome #2)
    """
    start = time.monotonic()
    while True:
        await asyncio.sleep(AGV_POLL_INTERVAL_S)

        # Safety lockout is non-fatal during navigation
        try:
            await _check_safety_periodic()
        except SafetyLockoutError as e:
            logger.warning("Safety lockout during navigation (non-fatal): %s", e)

        elapsed = time.monotonic() - start
        if elapsed > _AGV_NAVIGATION_HARD_TIMEOUT_S:
            raise DeviceConnectionError(
                f"AGV navigation timeout: {elapsed:.0f}s elapsed, target=({target_x:.3f},{target_y:.3f})"
            )

        # Primary signal: nav2adapter navigation status
        try:
            nav = await agv.navigation_status()
            ns = nav.get("status", "unknown")
            if ns == "arrived":
                logger.info("AGV arrived (navigation_status=arrived)")
                return
            if ns == "error":
                reason = nav.get("error_reason", "unknown")
                raise DeviceError(f"Navigation failed on AGV side: {reason}")
            if ns == "idle" and elapsed > 10:
                # idle after 10s means nav never started or was cancelled externally
                logger.warning("Navigation status=idle after %.0fs — checking pose fallback", elapsed)
        except (DeviceError, DeviceConnectionError):
            raise
        except Exception as _nav_err:
            logger.debug("navigation_status poll failed (falling back to pose): %s", _nav_err)

        # Fallback: pose-based distance check
        pose_resp = await agv.pose()
        pose = (pose_resp.get('pose') or {}) if isinstance(pose_resp, dict) else {}
        cur_x = float(pose.get('x_m') or 0.0)
        cur_y = float(pose.get('y_m') or 0.0)
        dist = math.hypot(target_x - cur_x, target_y - cur_y)

        logger.info("AGV navigating: dist=%.3fm | cur=(%.3f,%.3f) tgt=(%.3f,%.3f)",
                    dist, cur_x, cur_y, target_x, target_y)

        if dist < AGV_ARRIVAL_TOL_M:
            logger.info("AGV arrived: dist=%.3fm", dist)
            return

        # Online check
        try:
            st = await agv.status()
            if isinstance(st, dict) and st.get("online") is False:
                raise DeviceConnectionError("AGV went offline during navigation")
        except (DeviceConnectionError, DeviceError):
            raise
        except Exception as e:
            logger.warning("AGV status poll error (continuing): %s", e)


async def _ensure_drive_ready() -> None:
    """Enable drive mode and poll until drive_ready is confirmed."""
    _agv = _get_agv()
    try:
        await _agv.drive_mode(enable=True)
        logger.info("_ensure_drive_ready: drive_mode enable → OK")
    except Exception as e:
        logger.warning("_ensure_drive_ready: drive_mode enable failed: %s", e)

    for attempt in range(5):
        await asyncio.sleep(0.3)
        try:
            st = await _agv.status()
            if bool((st.get("state_flags") or {}).get("drive_ready")):
                logger.info("_ensure_drive_ready: drive_ready confirmed (poll %d)", attempt)
                return
        except Exception as e:
            logger.warning("_ensure_drive_ready: status poll error: %s", e)
    logger.warning("_ensure_drive_ready: drive_ready not confirmed after 1.5s, proceeding anyway")


async def _agv_microstep_to(target_x: float, target_y: float) -> None:
    """Drive AGV to target using teleop speed pulses via nav2adapter.

    Routes all commands through Nav2AdapterClient so nav2adapter's state
    store stays aware of teleop motion.
    """
    _agv = _get_agv()

    # Enable drive mode before teleop commands
    try:
        await _agv.drive_mode(enable=True)
        logger.info("AGV drive_mode enable → OK")
    except Exception as _e:
        logger.warning("AGV drive_mode enable failed: %s", _e)

    # Poll until drive_ready
    for _poll in range(5):
        await asyncio.sleep(0.3)
        try:
            _st = await _agv.status()
            _drive_ready = bool((_st.get("state_flags") or {}).get("drive_ready"))
            logger.info("AGV drive_ready poll %d: %s", _poll, _drive_ready)
            if _drive_ready:
                break
        except Exception as _se:
            logger.warning("AGV status poll error: %s", _se)
    else:
        logger.warning("AGV drive_ready not confirmed after 1.5s, proceeding anyway")

    try:
        dist = float("inf")
        prev_dist = float("inf")
        for step in range(AGV_MICROSTEP_MAX):
            await _check_safety_periodic()  # fail-closed mid-microstep
            pose_resp = await _agv.pose()
            pose = (pose_resp.get("pose") or {}) if isinstance(pose_resp, dict) else {}
            cur_x = float(pose.get("x_m") or 0.0)
            cur_y = float(pose.get("y_m") or 0.0)
            cur_theta = float(pose.get("theta_deg") or 0.0)
            dist = math.hypot(target_x - cur_x, target_y - cur_y)

            if dist < AGV_MICROSTEP_TOL_M:
                logger.info("AGV micro-step arrived: dist=%.3fm steps=%d", dist, step)
                break

            # Обнаружение проскока: если были близко и расстояние начало расти — стоп
            if step > 0 and dist > prev_dist and prev_dist < 0.10:
                logger.info("AGV micro-step overshot, stopping at dist=%.3fm (prev=%.3fm)", dist, prev_dist)
                break

            prev_dist = dist
            theta_rad = math.radians(cur_theta)
            fwd = (
                (target_x - cur_x) * math.cos(theta_rad)
                + (target_y - cur_y) * math.sin(theta_rad)
            )
            # Пропорциональная скорость: замедляемся при приближении к цели
            speed_mag = max(AGV_MICROSTEP_MIN_SPD,
                            min(AGV_MICROSTEP_MAX_SPD, AGV_MICROSTEP_K * dist))
            speed = speed_mag if fwd >= 0 else -speed_mag
            logger.info("AGV micro-step %d: dist=%.3fm speed=%.3f fwd=%.3f", step, dist, speed, fwd)

            try:
                await _agv.teleop_move(
                    speed=speed, angular_speed=0.0, duration=AGV_MICROSTEP_DUR,
                )
            except Exception as _e:
                logger.warning("AGV micro-step send failed: %s", _e)
            # POST возвращает немедленно — ждём пока AGV физически проедет и поза обновится
            await asyncio.sleep(AGV_MICROSTEP_DUR)
        else:
            logger.warning("AGV micro-step: max steps reached, last dist=%.3fm", dist)
    finally:
        # Send stop pulse then disable drive mode
        try:
            await _agv.teleop_move(speed=0.0, angular_speed=0.0, duration=AGV_MICROSTEP_DUR)
        except Exception as _e:
            logger.warning("AGV micro-step stop failed: %s", _e)
        try:
            await _agv.drive_mode(enable=False)
            logger.info("AGV drive_mode disable → OK")
        except Exception as _e:
            logger.warning("AGV drive_mode disable failed: %s", _e)


_xarm_commands = None  # set by app.state.startup() for WS emergency_stop


def set_xarm_commands(commands) -> None:
    """Inject xarm WS commands reference from app.state."""
    global _xarm_commands
    _xarm_commands = commands


_STOP_VERIFY_RETRIES = 2
_STOP_VERIFY_DELAY_S = 0.5


async def _stop_all_devices() -> None:
    """Stop ALL devices (AGV, arm, lift) in parallel, then verify they stopped."""

    async def _stop_agv():
        await asyncio.wait_for(agv.fault_reset(), timeout=3.0)

    async def _stop_arm():
        if _xarm_commands is not None:
            await asyncio.wait_for(_xarm_commands.stop(reason="cancel"), timeout=3.0)
        else:
            await asyncio.wait_for(manipulator.fault_reset(), timeout=3.0)

    async def _stop_lift():
        await asyncio.wait_for(lift.fault_reset(), timeout=3.0)

    # Phase 1: issue stop commands in parallel
    results = await asyncio.gather(
        _stop_agv(), _stop_arm(), _stop_lift(),
        return_exceptions=True,
    )
    for name, r in zip(["agv", "arm", "lift"], results):
        if isinstance(r, Exception):
            logger.warning("Stop %s failed: %s", name, r)
        else:
            logger.info("Stop %s: ok", name)

    # Phase 2: verify devices actually stopped (best-effort)
    for attempt in range(_STOP_VERIFY_RETRIES):
        await asyncio.sleep(_STOP_VERIFY_DELAY_S)
        still_moving = []
        try:
            st = await agv.status()
            vel = st.get("velocity", {}) if isinstance(st, dict) else {}
            if abs(float(vel.get("vx_m_s", 0) or 0)) > 0.01 or abs(float(vel.get("vy_m_s", 0) or 0)) > 0.01:
                still_moving.append("agv")
        except Exception:
            pass
        try:
            arm_st = await manipulator.status()
            if isinstance(arm_st, dict) and arm_st.get("state") == 1:
                still_moving.append("arm")
        except Exception:
            pass
        if not still_moving:
            logger.info("Stop-verify: all devices confirmed stopped (attempt %d)", attempt + 1)
            return
        logger.warning("Stop-verify attempt %d: still moving: %s — retrying stop", attempt + 1, still_moving)
        if "agv" in still_moving:
            try:
                await asyncio.wait_for(agv.fault_reset(), timeout=3.0)
            except Exception:
                pass
        if "arm" in still_moving:
            try:
                await _stop_arm()
            except Exception:
                pass
    logger.error("Stop-verify: devices may not have fully stopped after %d retries", _STOP_VERIFY_RETRIES)


# ---------------------------------------------------------------------------
# Phase functions for move_robot_to_product
# ---------------------------------------------------------------------------

async def _phase_preflight(ctx: _NavigationContext) -> None:
    """PHASE 1: Recover devices, arm→JOB_POSE, lift down.

    All errors here are FATAL — we haven't started moving yet,
    so aborting is safe (outcome #1: не стартанул).
    """
    _set_phase(TaskPhase.PREFLIGHT)
    logger.info("move_robot_to_product: phase=PREFLIGHT same_shelf=False")
    ctx.velocity = await _preflight_for_navigation("move_robot_to_product")
    await _check_safety_periodic()
    await _assert_arm_stowed_for_transport()


async def _phase_navigate(ctx: _NavigationContext) -> None:
    """PHASE 2: Drive AGV to target (go_to_pose or micro-step).

    DeviceError (HTTP 4xx — transport rejected) is FATAL: AGV won't move.
    DeviceConnectionError (timeout) is non-fatal: command likely accepted.
    SafetyLockoutError during polling is non-fatal: relay flicker.
    """
    _set_phase(TaskPhase.NAVIGATE)

    # Get current pose to decide navigation strategy
    _nav_pose = await agv.pose()
    _nav_p = (_nav_pose.get("pose") or {}) if isinstance(_nav_pose, dict) else {}
    _cur_x = float(_nav_p.get("x_m") or 0.0)
    _cur_y = float(_nav_p.get("y_m") or 0.0)
    _cur_th = float(_nav_p.get("theta_deg") or 0.0)
    _dist = math.hypot(ctx.target_x - _cur_x, ctx.target_y - _cur_y)
    _theta_rad = math.radians(_cur_th)
    _fwd = (
        (ctx.target_x - _cur_x) * math.cos(_theta_rad)
        + (ctx.target_y - _cur_y) * math.sin(_theta_rad)
    )
    _use_microstep = _fwd < 0 and _dist < AGV_MICROSTEP_DIST_M

    if _use_microstep:
        logger.info("move_robot_to_product: phase=NAVIGATING micro-step backward dist=%.3fm fwd=%.3f",
                    _dist, _fwd)
        await _agv_microstep_to(ctx.target_x, ctx.target_y)
    else:
        logger.info("move_robot_to_product: phase=NAVIGATING → (%.3f, %.3f)", ctx.target_x, ctx.target_y)
        await _ensure_drive_ready()
        # go_to_pose(wait=False) is fire-and-forget: nav2adapter may block
        # the HTTP response while waiting for scanner (up to 30s).
        #
        # DeviceConnectionError (timeout/unreachable) is non-fatal:
        # the command was likely accepted, AGV will drive, and
        # _wait_agv_arrival will confirm actual arrival.
        #
        # DeviceError (HTTP 4xx — e.g. 409 transport_creation_failed)
        # is FATAL: nav2adapter explicitly rejected the command,
        # AGV will NOT move, and polling would hang for 5 minutes.
        try:
            await agv.go_to_pose(
                x_m=ctx.target_x, y_m=ctx.target_y,
                theta_deg=ctx.target_theta, map_id=ctx.target_map,
                wait=False,
            )
        except DeviceConnectionError as _nav_err:
            logger.warning("go_to_pose timeout (non-fatal, will poll arrival): %s", _nav_err)
        await _wait_agv_arrival(ctx.target_x, ctx.target_y)


async def _phase_post_navigate(ctx: _NavigationContext) -> None:
    """Post-navigate safety and stop checks — all non-fatal.

    DO NOT re-raise these exceptions.  Doing so kills POSITIONING
    and the operator has to manually fix the robot.

    1) SafetyLockoutError: relay can flicker for seconds after AGV stops.
    2) DeviceReadyError: after micro-step the velocity sensor reports
       ~0.03 m/s residual for 10+ seconds (sensor noise).
    """
    logger.info("move_robot_to_product: post-navigate → AGV stop verify")
    try:
        await _check_safety_periodic()
    except SafetyLockoutError as _sl:
        logger.warning("safety lockout after navigate (non-fatal): %s", _sl)
    try:
        await _assert_agv_stopped()
    except DeviceReadyError as _dr:
        logger.warning("AGV not fully stopped (non-fatal, proceeding): %s", _dr)
    logger.info("move_robot_to_product: post-navigate checks passed → entering POSITIONING")


async def _phase_same_shelf_prep(ctx: _NavigationContext) -> None:
    """AGV already at target — just reset arm to JOB_POSE baseline."""
    _set_phase(TaskPhase.PREFLIGHT)
    logger.info("move_robot_to_product: phase=PREFLIGHT same_shelf=True → JOB_POSE baseline")
    await _move_to_job_pose(ctx.velocity)


async def _phase_position(ctx: _NavigationContext) -> None:
    """PHASE 3: Move lift and arm to target positions (parallel)."""
    _set_phase(TaskPhase.POSITION)
    _LIFT_TOL = 500   # encoder units (~5 mm)

    # Reset any fault that may have accumulated during navigation
    try:
        await lift.fault_reset()
    except Exception as _fr_err:
        logger.warning("lift fault_reset pre-POSITIONING (non-fatal): %s", _fr_err)

    lift_cm = ctx.lift_units / 1000.0
    logger.info("move_robot_to_product: phase=POSITIONING lift→%d (%.2f cm)", ctx.lift_units, lift_cm)

    joints_data = getattr(ctx.xarm_joints, 'joints', None) if ctx.xarm_joints else None
    if joints_data:
        j_params = XarmMoveWithJointsDictParams(
            points=[XarmJointsDict(
                j1=float(getattr(joints_data, 'j1', 0.0)),
                j2=float(getattr(joints_data, 'j2', 0.0)),
                j3=float(getattr(joints_data, 'j3', 0.0)),
                j4=float(getattr(joints_data, 'j4', 0.0)),
                j5=float(getattr(joints_data, 'j5', 0.0)),
                j6=float(getattr(joints_data, 'j6', 0.0)),
            )],
            velocity_percent=ctx.velocity,
            reset_faults=False,
        )
        logger.info("move_robot_to_product: POSITIONING → parallel lift+arm j1=%.2f j2=%.2f",
                    getattr(joints_data, 'j1', 0.0), getattr(joints_data, 'j2', 0.0))
        await asyncio.gather(
            igus_move_and_check(ctx.lift_units, ctx.velocity),
            asyncio.wait_for(manipulator.complex_move_with_joints(j_params), timeout=60),
        )
    else:
        logger.info("move_robot_to_product: POSITIONING → lift only (no joints data)")
        await igus_move_and_check(ctx.lift_units, ctx.velocity)


async def _phase_verify(ctx: _NavigationContext) -> None:
    """PHASE 4: Verify lift and arm reached targets, retry once if not."""
    _set_phase(TaskPhase.VERIFY)
    _LIFT_TOL = 500   # encoder units (~5 mm)
    _JOINT_TOL = 3.0  # degrees per joint

    # Verify lift
    lift_ok = False
    try:
        pos_r = await lift.position()
        actual_lift = float((pos_r or {}).get('position', 0))
        lift_ok = abs(actual_lift - ctx.lift_units) <= _LIFT_TOL
        logger.info("POSITIONING verify: lift actual=%.0f target=%d ok=%s",
                    actual_lift, ctx.lift_units, lift_ok)
    except Exception as e:
        logger.warning("POSITIONING verify: lift position read failed: %s", e)

    # Verify arm
    arm_ok = False
    joints_data = getattr(ctx.xarm_joints, 'joints', None) if ctx.xarm_joints else None
    if joints_data:
        try:
            cj_resp = await manipulator.current_joints_position()
            cj = (cj_resp or {}).get('joints', {})
            arm_ok = all(
                abs(float(cj.get(k, 0)) - float(getattr(joints_data, k, 0))) <= _JOINT_TOL
                for k in ('j1', 'j2', 'j3', 'j4', 'j5', 'j6')
            )
            logger.info("POSITIONING verify: arm j1=%.2f target=%.2f ok=%s",
                        float(cj.get('j1', 0)), float(getattr(joints_data, 'j1', 0)), arm_ok)
        except Exception as e:
            logger.warning("POSITIONING verify: arm joints read failed: %s", e)
    else:
        arm_ok = True  # no joints to verify

    # Retry if needed
    if not lift_ok:
        logger.info("POSITIONING verify: lift not at target → retry")
        await igus_move_and_check(ctx.lift_units, ctx.velocity)
    if joints_data and not arm_ok:
        logger.info("POSITIONING verify: arm not at target → retry")
        j_params = XarmMoveWithJointsDictParams(
            points=[XarmJointsDict(
                j1=float(getattr(joints_data, 'j1', 0.0)),
                j2=float(getattr(joints_data, 'j2', 0.0)),
                j3=float(getattr(joints_data, 'j3', 0.0)),
                j4=float(getattr(joints_data, 'j4', 0.0)),
                j5=float(getattr(joints_data, 'j5', 0.0)),
                j6=float(getattr(joints_data, 'j6', 0.0)),
            )],
            velocity_percent=ctx.velocity,
            reset_faults=False,
        )
        await asyncio.wait_for(manipulator.complex_move_with_joints(j_params), timeout=60)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@guarded_async_call(robot_lock)
async def move_robot_to_product(params) -> bool:
    """Orchestrated AGV navigation + lift/arm positioning.

    Phases: PREFLIGHT → NAVIGATE → POST-NAVIGATE → POSITION → VERIFY.
    Each phase is an independent function with its own error policy.
    See _phase_* functions above for details.
    """
    ctx = _NavigationContext.from_params(params)
    location = getattr(params, 'location', None)

    try:
        ctx.same_shelf = location is not None and await agv_is_near(
            agv, float(location.x_m), float(location.y_m),
        )

        if not ctx.same_shelf:
            await _phase_preflight(ctx)
            await _phase_navigate(ctx)
            await _phase_post_navigate(ctx)
        else:
            await _phase_same_shelf_prep(ctx)

        await _phase_position(ctx)
        await _phase_verify(ctx)

        logger.info("move_robot_to_product: phase=DONE")
        return True
    except asyncio.CancelledError:
        logger.warning("move_robot_to_product: cancelled (phase=%s) → stopping all devices",
                        _current_phase.value)
        await _stop_all_devices()
        raise
    finally:
        _set_phase(TaskPhase.IDLE)


@guarded_async_call(robot_lock)
async def go_to_charging_station(station_id: int) -> bool:
    """Оркестрированная отправка на зарядную станцию.

    PREFLIGHT — arm→JOB_POSE, lift↓
    CLEAR     — очистить транспорты (symovo.fault_reset)
    ACTIVATE  — включить зарядную станцию → Symovo запускает скрипт заезда
    """
    try:
        v = _get_global_velocity()

        # ── PREFLIGHT ──────────────────────────────────────────────────────
        _set_phase(TaskPhase.PREFLIGHT)
        logger.info("go_to_charging: phase=PREFLIGHT station_id=%s", station_id)
        await _preflight_for_navigation("go_to_charging")

        # Verify arm stowed before AGV moves
        await _assert_arm_stowed_for_transport()

        # ── CLEAR TRANSPORTS ──────────────────────────────────────────────
        _set_phase(TaskPhase.CLEANUP)
        logger.info("go_to_charging: phase=CLEAR_TRANSPORTS")
        try:
            await agv.fault_reset()
        except Exception as e:
            logger.warning("go_to_charging: clear transports failed (continuing): %s", e)

        # ── ACTIVATE ──────────────────────────────────────────────────────
        _set_phase(TaskPhase.EXECUTE)
        logger.info("go_to_charging: phase=ACTIVATE station_id=%s", station_id)
        await agv.go_to_charging_station(station_id)

        # ── RELEASE DRIVE MODE ────────────────────────────────────────────
        logger.info("go_to_charging: phase=RELEASE_DRIVE_MODE")
        try:
            await _get_agv().drive_mode(enable=False)
            logger.info("go_to_charging: drive_mode disable → OK")
        except Exception as e:
            logger.warning("go_to_charging: drive_mode disable failed: %s", e)

        logger.info("go_to_charging: phase=DONE — charger activated, Symovo docking script started")
        return True
    finally:
        _set_phase(TaskPhase.IDLE)


async def record_current_position() -> dict:
    """Read current lift height, xarm joints and symovo pose in parallel."""

    async def _lift_pos():
        try:
            return await lift.position()
        except Exception as e:
            return {"error": str(e)}

    async def _xarm_joints():
        try:
            return await manipulator.current_joints_position()
        except Exception as e:
            return {"error": str(e)}

    async def _symovo_pose():
        try:
            data = await agv.pose()
            pose = data.get("pose", {}) if isinstance(data, dict) else {}
            return {
                "x_m": pose.get("x_m"),
                "y_m": pose.get("y_m"),
                "theta_deg": pose.get("theta_deg"),
                "map_id": pose.get("map_id"),
            }
        except Exception as e:
            return {"error": str(e)}

    lift_pos, joints, vehicle_pose = await asyncio.gather(
        _lift_pos(), _xarm_joints(), _symovo_pose()
    )

    return {
        "lift": lift_pos,
        "xarm_joints": joints,
        "vehicle_pose": vehicle_pose,
    }


async def get_robot_system_status() -> dict:
    async def fetch_xarm_state():
        # Cache-only xArm status: do not trigger extra manipulator status requests from /status.
        cached = xarm_status.get_status()
        last_updated_ts = xarm_status.get_last_updated_ts()
        if isinstance(cached, dict) and all(k in cached for k in ("connected", "has_error", "has_err_warn")):
            now = time.time()
            age_s = max(0.0, now - float(last_updated_ts or 0.0))
            stale = bool(last_updated_ts <= 0.0 or age_s > float(XARM_STATUS_CACHE_TTL_SEC))
            state = dict(cached)
            state["stale"] = stale
            state["cache_age_s"] = round(age_s, 3)
            state["cache_ttl_s"] = float(XARM_STATUS_CACHE_TTL_SEC)
            return state
        return ErrorStatus(
            error={
                "type": "xarm_cache_unavailable",
                "msg": "No devices_status_report in cache yet",
            }
        )

    async def fetch_igus_state():
        try:
            state = await lift.status()
            return state
        except Exception as e:
            return ErrorStatus(error={"type": type(e).__name__, "msg": str(e)})

    async def fetch_symovo_state():
        try:
            return await agv.status()
        except Exception as e:
            return ErrorStatus(error={"type": type(e).__name__, "msg": str(e)})
        
    results = await asyncio.gather(
        fetch_xarm_state(), fetch_igus_state(), fetch_symovo_state(), return_exceptions=True
    )

    # Handle exceptions
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            results[i] = ErrorStatus(error={"type": type(result).__name__, "msg": str(result)})

    # Check subsystem readiness. results[i] can be dict, response model, or ErrorStatus — do not use ["key"] on ErrorStatus.
    def _get(obj, key: str, default=None):
        if isinstance(obj, ErrorStatus):
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    symovo_ready = (
        isinstance(results[2], dict)
        and not isinstance(results[2], ErrorStatus)
        and _get(results[2], "online", False)
        and _get(results[2], "enabled", False)
    )

    if isinstance(results[0], ErrorStatus):
        xarm_ready = False
    else:
        xarm_ready = bool(
            _get(results[0], "connected") and not _get(results[0], "has_error", True) and not _get(results[0], "has_err_warn", True) and not _get(results[0], "stale", True)
        )

    if isinstance(results[1], ErrorStatus):
        igus_ready = False
    else:
        igus_ready = bool(
            _get(results[1], "connected") and _get(results[1], "homed") and not _get(results[1], "error", True)
        )

    ready = xarm_ready and igus_ready and symovo_ready
    if not ready:
        missing = []
        if not xarm_ready:
            missing.append("XArm")
        if not igus_ready:
            missing.append("Igus")
        if not symovo_ready:
            missing.append("AGV")
        message = "{} not ready".format(", ".join(missing)) if missing else "Devices not ready"
    else:
        message = ""

    dump_summary = results[0] if isinstance(results[0], dict) else xarm_status.get_status()
    dump_raw = xarm_status.get_raw()
    xarm_dump = dump_summary
    if isinstance(dump_raw, dict):
        xarm_dump = dict(dump_raw)
        if isinstance(dump_summary, dict):
            xarm_dump["summary"] = dump_summary
        xarm_dump["updated_ts"] = xarm_status.get_last_updated_ts()
    elif xarm_dump is None:
        xarm_dump = results[0]

    return {
        "ready": ready,
        "message": message,
        "igus": results[1],
        "symovo": results[2],
        "xarm": xarm_dump,
    }

async def main():
    await lift.reference()
    await fault_reset()
    
    await move_robot_to_box_1(40)


if __name__ == "__main__":
    asyncio.run(main())