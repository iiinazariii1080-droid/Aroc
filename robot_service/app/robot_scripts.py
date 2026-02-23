
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from db.trajectory import get_trajectory, save_trajectory, calibrate_distance, init_trajectory_table
import math
import asyncio
import time
from app.config import IGUS_CONTAINER_IP, IGUS_CONTAINER_PORT, XARM_CONTAINER_IP, XARM_CONTAINER_PORT, SYMOVO_CONTAINER_IP, DEPTH_CAMERA_CONTAINER_IP, DEPTH_CAMERA_CONTAINER_PORT, XARM_STATUS_CACHE_TTL_SEC
from services.igus_service import IgusMotorClient
from services.xarm_service import XarmManipulatorClient
from app import xarm_status
from services.camera_service import CameraClient
from models.api_types import (IgusMoveParams, XarmStatusResponse, IgusStatusResponse, XarmMoveWithToolParams, SymovoStatusResponse, ErrorStatus,XarmMoveWithJointsDictParams,IgusMoveResult, XarmMoveResult,XarmJointsDict,XarmMoveWithJointsParams)
import logging
import models.xarm_positions as xarm_positions
logger = logging.getLogger(__name__)
import asyncio
from exceptions import RobotError, DeviceReadyError, DeviceConnectionError
from app.decorator import*
from services.symovo_service import SymovoAgvClient, _normalize_symovo_status

def _ns_to_dict(obj):
    try:
        from types import SimpleNamespace
        if isinstance(obj, SimpleNamespace):
            return {k: _ns_to_dict(v) for k, v in obj.__dict__.items()}
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
        if isinstance(obj, (list, tuple)):
            return [_ns_to_dict(v) for v in obj]
        if isinstance(obj, dict):
            return {k: _ns_to_dict(v) for k, v in obj.items()}
        return obj
    except Exception:
        return str(obj)

lift_url = f"http://{IGUS_CONTAINER_IP}:{IGUS_CONTAINER_PORT}"
lift = IgusMotorClient(lift_url)

symovo_base_url = f"https://{SYMOVO_CONTAINER_IP}/v0"
symovo = SymovoAgvClient(symovo_base_url)

manipulator_url = f"http://{XARM_CONTAINER_IP}:{XARM_CONTAINER_PORT}"
manipulator = XarmManipulatorClient(manipulator_url)

depth_camera_url = f"http://{DEPTH_CAMERA_CONTAINER_IP}:{DEPTH_CAMERA_CONTAINER_PORT}"
depth_camera = CameraClient(depth_camera_url)
robot_lock = asyncio.Lock()

async def fault_reset() -> bool:
    try:
        await asyncio.gather(
            lift.fault_reset(),
            manipulator.fault_reset(),
            symovo.fault_reset()
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

async def _preflight_make_transport_safe(params) -> None:
    v = float(getattr(params, 'velocity_percent', 20.0) or 20.0)
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

@guarded_async_call(robot_lock)
async def autotake(velocity: int) -> bool:
    MAX_TOOL_OFFSET_MM = 300.0  # safety cap for any single tool-frame move

    try:
        # Ensure xarm motion is enabled before any movement
        await manipulator.enable_motion()

        # Initialize database table if it doesn't exist
        init_trajectory_table()

        # Try to get depth from camera, use default if unavailable
        try:
            # depth API expects [0..100]
            forward_distance = await depth_camera.depth(x_norm=50, y_norm=50)
            raw_distance = forward_distance['depth'] * 1000
            if raw_distance is None:
                raise RuntimeError("Depth camera reading is None")
            distance = calibrate_distance(raw_distance)
        except Exception as e:
            logger.warning("Depth camera unavailable, using default distance: %s", e)
            distance = 0
            return False

        config = get_trajectory()

        # Optional depth compensation from trajectory config (for camera/geometry bias tuning)
        # Example:
        # "depthCompensation": {"scale": 1.0, "bias": 0.0, "quad": 0.0}
        depth_comp = config.get("depthCompensation", {}) if isinstance(config, dict) else {}
        depth_scale = float(depth_comp.get("scale", 1.0))
        depth_bias = float(depth_comp.get("bias", 0.0))
        depth_quad = float(depth_comp.get("quad", 0.0))
        compensated_distance = (distance * depth_scale) + depth_bias + (depth_quad * (distance ** 2))
        logger.info(
            "Autotake depth: raw=%.1f calibrated=%.1f compensated=%.1f (scale=%.4f bias=%.2f quad=%.6f)",
            raw_distance,
            distance,
            compensated_distance,
            depth_scale,
            depth_bias,
            depth_quad,
        )
        distance = compensated_distance
        if distance > 1000.0:
            raise RuntimeError("Calculated distance %.1f mm exceeds 1000 mm limit" % distance)
        elif distance < 100.0:
            raise RuntimeError("Calculated distance %.1f mm below 100 mm limit" % distance)

        # Track whether the controller may need a fault reset before next move
        needs_fault_reset = False
        applied_base_move = None

        if config['prefix']['active']:
            prefix_position = XarmMoveWithToolParams(x_offset_mm=config['prefix']['posX'],
                                                    y_offset_mm=config['prefix']['posY'],
                                                    z_offset_mm=config['prefix']['posZ'],
                                                    velocity_percent=velocity,
                                                    reset_faults=False)
            await manipulator.change_tool_position(prefix_position)

        if config['gripper']['active']:
            try:
                await manipulator.gripper_take()

                # Robust grip verification to avoid false "grip success"
                verify_cfg = config.get("gripperVerify", {}) if isinstance(config, dict) else {}
                verify_enabled = bool(verify_cfg.get("enabled", True))
                if verify_enabled:
                    sample_count = max(1, int(verify_cfg.get("samples", 3)))
                    required_positive = max(1, min(sample_count, int(verify_cfg.get("required", 2))))
                    interval_ms = max(50, int(verify_cfg.get("intervalMs", 120)))
                    max_errors = max(1, int(verify_cfg.get("maxReadErrors", 2)))
                    max_recoveries = max(0, int(verify_cfg.get("maxAutoRecoveries", 1)))
                    allow_unknown_when_sensor_unsupported = bool(
                        verify_cfg.get("allowUnknownWhenSensorUnsupported", True)
                    )
                    max_attempts = sample_count + max_errors + max_recoveries

                    positives = 0
                    last_feedback = "UNKNOWN"
                    read_errors = 0
                    valid_samples = 0
                    recoveries = 0
                    unknown_samples = 0
                    sensor_supported = True
                    for index in range(max_attempts):
                        if index > 0:
                            await asyncio.sleep(interval_ms / 1000.0)
                        try:
                            gs = await manipulator.gripper_status()
                        except Exception as read_exc:
                            read_errors += 1
                            msg = str(read_exc).lower()
                            is_fault_like = ("faulted" in msg) or ("controllererror" in msg) or ("c19" in msg)
                            if is_fault_like and recoveries < max_recoveries:
                                recoveries += 1
                                logger.warning(
                                    "Autotake grip verify: fault-like read error -> auto recover %d/%d: %s",
                                    recoveries,
                                    max_recoveries,
                                    read_exc,
                                )
                                try:
                                    await manipulator.fault_reset()
                                    await manipulator.enable_motion()
                                except Exception as recover_exc:
                                    logger.warning("Autotake grip verify: auto recover failed: %s", recover_exc)
                                continue
                            logger.warning(
                                "Autotake grip verify: gripper_status read error %d/%d: %s",
                                read_errors,
                                max_errors,
                                read_exc,
                            )
                            if read_errors > max_errors:
                                raise RuntimeError(
                                    f"Gripper status unstable during verify: {read_errors} read errors"
                                ) from read_exc
                            continue
                        sensor_supported = bool(gs.get("sensor_supported", True))
                        last_feedback = str(gs.get("feedback", "UNKNOWN"))
                        if last_feedback == "UNKNOWN" and recoveries < max_recoveries:
                            recoveries += 1
                            logger.warning(
                                "Autotake grip verify: feedback UNKNOWN -> auto recover %d/%d",
                                recoveries,
                                max_recoveries,
                            )
                            try:
                                await manipulator.fault_reset()
                                await manipulator.enable_motion()
                            except Exception as recover_exc:
                                logger.warning("Autotake grip verify: auto recover failed: %s", recover_exc)
                            continue
                        valid_samples += 1
                        if last_feedback == "UNKNOWN":
                            unknown_samples += 1
                        if last_feedback == "PART_GRIPPED":
                            positives += 1
                        if valid_samples >= sample_count:
                            break

                    logger.info(
                        "Autotake grip verify: positives=%d/%d required=%d last_feedback=%s read_errors=%d/%d",
                        positives,
                        valid_samples,
                        required_positive,
                        last_feedback,
                        read_errors,
                        max_errors,
                    )
                    if valid_samples < sample_count:
                        raise RuntimeError(
                            "Vacuum verify incomplete: only %d/%d valid samples"
                            % (valid_samples, sample_count)
                        )
                    if positives < required_positive:
                        if (
                            allow_unknown_when_sensor_unsupported
                            and not sensor_supported
                            and valid_samples >= sample_count
                            and unknown_samples == valid_samples
                        ):
                            logger.warning(
                                "Autotake grip verify degraded accept: sensor unsupported, UNKNOWN samples=%d/%d",
                                unknown_samples,
                                valid_samples,
                            )
                        else:
                            raise RuntimeError(
                                "Vacuum grip not confirmed: PART_GRIPPED %d/%d (<%d), last=%s"
                                % (positives, valid_samples, required_positive, last_feedback)
                            )
            except Exception as e:
                logger.warning("Gripper take/verify failed: %s — will reset faults before next move", e)
                needs_fault_reset = True
                raise

        if config['baseMove']['active']:
            z_total = config['baseMove']['posZ'] + distance
            x_off = config['baseMove']['posX']
            y_off = config['baseMove']['posY']
            logger.info(
                "Autotake baseMove: x=%.1f y=%.1f z=%.1f (posZ=%.1f + distance=%.1f)",
                x_off, y_off, z_total, config['baseMove']['posZ'], distance,
            )
            # Clamp each axis to prevent OUT_OF_WORKSPACE
            x_off = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, x_off))
            y_off = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, y_off))
            z_total = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, z_total))
            # Adaptive fallback for workspace envelope: shrink move vector on OUT_OF_WORKSPACE
            base_move_scales = (1.0, 0.85, 0.7, 0.55, 0.4, 0.25)
            last_workspace_error = None
            for scale in base_move_scales:
                sx = x_off * scale
                sy = y_off * scale
                sz = z_total * scale
                try:
                    take_position = XarmMoveWithToolParams(x_offset_mm=sx,
                                                            y_offset_mm=sy,
                                                            z_offset_mm=sz,
                                                            velocity_percent=velocity,
                                                            reset_faults=needs_fault_reset)
                    await manipulator.change_tool_position(take_position)
                    applied_base_move = (sx, sy, sz)
                    if scale < 1.0:
                        logger.warning(
                            "Autotake baseMove reduced by scale=%.2f to stay in workspace: x=%.1f y=%.1f z=%.1f",
                            scale, sx, sy, sz,
                        )
                    break
                except Exception as e:
                    if "OUT_OF_WORKSPACE" in str(e):
                        last_workspace_error = e
                        continue
                    raise
            if applied_base_move is None:
                if last_workspace_error is not None:
                    raise last_workspace_error
                raise RuntimeError("Autotake baseMove failed without accepted fallback")
            needs_fault_reset = False

        if config['postfix']['active']:
            postfix_position = XarmMoveWithToolParams(x_offset_mm=config['postfix']['posX'],
                                                    y_offset_mm=config['postfix']['posY'],
                                                    z_offset_mm=config['postfix']['posZ'],
                                                    velocity_percent=velocity,
                                                    reset_faults=needs_fault_reset)
            await manipulator.change_tool_position(postfix_position)
            needs_fault_reset = False

        if config['return']['active']:
            if applied_base_move is not None:
                x_ret, y_ret, z_ret = -applied_base_move[0], -applied_base_move[1], -applied_base_move[2]
            else:
                z_total_base = config['baseMove']['posZ'] + distance
                z_total_base = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, z_total_base))
                x_ret = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, -(config['baseMove']['posX'])))
                y_ret = max(-MAX_TOOL_OFFSET_MM, min(MAX_TOOL_OFFSET_MM, -(config['baseMove']['posY'])))
                z_ret = -z_total_base

            take_position = XarmMoveWithToolParams(x_offset_mm=x_ret,
                                                    y_offset_mm=y_ret,
                                                    z_offset_mm=z_ret,
                                                    velocity_percent=velocity,
                                                    reset_faults=needs_fault_reset)
            await manipulator.change_tool_position(take_position)
            needs_fault_reset = False

        logger.info("Autotake completed successfully")
        return True
    except Exception as e:
        logger.error("Autotake failed: %s", e)
        raise DeviceConnectionError(f"Autotake operation failed: {e}") from e

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

@guarded_async_call(robot_lock)
async def move_robot_to_box_1(velocity: int) -> bool:
    if await igus_move_and_check(30000, velocity/2):
        await asyncio.sleep(0)
        params = xarm_positions.get_XarmMoveWithJointsDictParams_with_box_num(1)
        params.velocity_percent=velocity
        await asyncio.wait_for(manipulator.complex_move_with_joints(params), timeout=60)
        return True

@guarded_async_call(robot_lock)
async def move_robot_to_box_2(velocity: int) -> bool:
    if await igus_move_and_check(30000, velocity/2):
        await asyncio.sleep(0)
        params = xarm_positions.get_XarmMoveWithJointsDictParams_with_box_num(2)
        params.velocity_percent=velocity
        await asyncio.wait_for(manipulator.complex_move_with_joints(params), timeout=60)
        return True
    
@guarded_async_call(robot_lock)
async def move_to_transport_position(velocity: int) -> bool:
    if await igus_move_and_check(20000, velocity):
        await asyncio.sleep(0)
        current_pose = await manipulator.current_joints_position()
        if current_pose['name'] != "TRANSPORT_STEP_2":
            params = xarm_positions.get_XarmMoveWithJointsDictParams_for_transport_position()
            await asyncio.wait_for(manipulator.complex_move_with_joints(params), timeout=15)
            await asyncio.sleep(0)
        if await igus_move_and_check(0, velocity):
            return True

@guarded_async_call(robot_lock)
async def move_robot_to_product(params) -> dict:
    logger.info("move_robot_to_product: start | params=%s", _ns_to_dict(params))
    # Ready gate: optionally reset/reference
    if bool(getattr(params, 'reset_faults', False)):
        logger.info("move_robot_to_product: reset_faults=True -> calling set_ready()")
        await set_ready()

    async def fetch_xarm_state():
        try:
            state = await manipulator.status()
            return state
        except Exception as e:
            return ErrorStatus(error={"type": type(e).__name__, "msg": str(e)})

    async def fetch_igus_state():
        try:
            state = await lift.status()
            return state
        except Exception as e:
            return ErrorStatus(error={"type": type(e).__name__, "msg": str(e)})

    async def fetch_symovo_state():
        try:
            state = await symovo.status()
            return _normalize_symovo_status(state)
        except Exception as e:
            return ErrorStatus(error={"type": type(e).__name__, "msg": str(e)})
        
    logger.info("move_robot_to_product: fetching devices status...")
    results = await asyncio.gather(
        fetch_xarm_state(), fetch_igus_state(), fetch_symovo_state(), return_exceptions=True
    )
    await asyncio.sleep(0)
    logger.info("move_robot_to_product: status fetched: types=%s",
                [type(r).__name__ for r in results])

    # Handle exceptions
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            results[i] = ErrorStatus(error={"type": type(result).__name__, "msg": str(result)})

    # Compute readiness of subsystems
    symovo_ready = (
        isinstance(results[2], SymovoStatusResponse)
        and results[2].online
        and results[2].enabled
    )

    xarm_ready = bool(isinstance(results[0], dict) and results[0].get("connected") and not results[0].get("has_error") and not results[0].get("has_err_warn"))
    igus_ready = bool(isinstance(results[1], dict) and results[1].get("connected") and results[1].get("homed") and not results[1].get("error"))
    logger.info("move_robot_to_product: readiness | xarm=%s igus=%s agv=%s", xarm_ready, igus_ready, symovo_ready)

    # Preflight: make system transport-safe before any movement
    await _preflight_make_transport_safe(params)

    # Decide requested operations
    # Manipulator offsets considered only if any component differs from 0 by small eps
    _offs = getattr(params, 'manipulator_offsets', None)
    need_xarm = bool(_offs and (abs(float(_offs.x_offset_mm or 0.0)) > 1e-6 or abs(float(_offs.y_offset_mm or 0.0)) > 1e-6 or abs(float(_offs.z_offset_mm or 0.0)) > 1e-6))

    need_igus = getattr(params, 'lift_position_cm', None) is not None

    # AGV movement only if requested location differs from current by noticeable amount
    need_agv = False
    _loc = getattr(params, 'location', None)
    if _loc:
        # Sentinel: location all zeros => treat as "no AGV move" request
        tgt_x = float(getattr(_loc, 'x_m', 0.0) or 0.0)
        tgt_y = float(getattr(_loc, 'y_m', 0.0) or 0.0)
        tgt_th = float(getattr(_loc, 'theta_deg', 0.0) or 0.0)
        tgt_map = int(getattr(_loc, 'map_id', 0) or 0)
        if not (abs(tgt_x) <= 1e-6 and abs(tgt_y) <= 1e-6 and abs(tgt_th) <= 1e-6 and tgt_map == 0):
            try:
                initial = await symovo.pose()
                init_pose = (initial.get('pose') or {}) if isinstance(initial, dict) else {}
                init_x = float(init_pose.get('x_m') or 0.0)
                init_y = float(init_pose.get('y_m') or 0.0)
                planned_dx = tgt_x - init_x
                planned_dy = tgt_y - init_y
                planned_dist = math.hypot(planned_dx, planned_dy)
                need_agv = planned_dist > 1e-6
            except Exception:
                # If cannot get pose, fallback: consider AGV needed only if target not zeros
                need_agv = True

    logger.info("move_robot_to_product: needs | agv=%s igus=%s xarm=%s", need_agv, need_igus, need_xarm)

    missing = []
    if need_agv and not symovo_ready:
        missing.append("AGV")
    if need_igus and not igus_ready:
        missing.append("Igus")
    if need_xarm and not xarm_ready:
        missing.append("XArm")

    if missing:
        # Attempt auto-prepare IGUS if it's the only blocker and we need IGUS
        if missing == ["Igus"] and need_igus:
            try:
                logger.info("move_robot_to_product: attempting IGUS auto-prepare (fault_reset + reference)")
                await lift.fault_reset()
                await lift.reference()
                st = await lift.status()
                igus_ready = bool(isinstance(st, dict) and st.get("connected") and st.get("homed") and not st.get("error"))
                if igus_ready:
                    logger.info("move_robot_to_product: IGUS auto-prepare succeeded")
                    missing = [m for m in missing if m != "Igus"]
                else:
                    logger.error("move_robot_to_product: IGUS auto-prepare failed, status=%s", st)
            except Exception as e:
                logger.error("move_robot_to_product: IGUS auto-prepare exception: %s", e)

        if missing:
            logger.error("move_robot_to_product: missing subsystems: %s", missing)
            raise DeviceReadyError("Not ready: " + ", ".join(missing))

    # Execute AGV move first (if requested and ready)
    if need_agv:
        # Ensure manipulator in transport-safe pose before AGV move
        logger.info("move_robot_to_product: AGV requested -> ensure manipulator transport pose")
        current_pose = await manipulator.current_joints_position()
        logger.info("move_robot_to_product: current joints pose: %s", current_pose)
        # if current_pose.get('name') != "TRANSPORT_STEP_2":
        #     _params = xarm_positions.get_XarmMoveWithJointsDictParams_for_transport_position()
        #     await asyncio.wait_for(manipulator.complex_move_with_joints(_params), timeout=15)
        #     await asyncio.sleep(0)
        #     logger.info("move_robot_to_product: manipulator moved to transport pose")

        # Capture initial AGV pose for relative tolerance calculation
        logger.info("move_robot_to_product: querying AGV initial pose")
        initial = await symovo.pose()
        init_pose = (initial.get('pose') or {}) if isinstance(initial, dict) else {}
        init_x = float(init_pose.get('x_m') or 0.0)
        init_y = float(init_pose.get('y_m') or 0.0)

        target_x = float(params.location.x_m or 0.0)
        target_y = float(params.location.y_m or 0.0)
        target_th = float(getattr(params.location, 'theta_deg', 0.0) or 0.0)
        target_map_id = getattr(params.location, 'map_id', None)

        planned_dx = target_x - init_x
        planned_dy = target_y - init_y
        planned_dist = math.hypot(planned_dx, planned_dy)

        linear_eps = 1e-6
        if planned_dist > linear_eps:
            logger.info(
                "AGV target: x=%.3f, y=%.3f, theta=%.2f°, map_id=%s | initial: x=%.3f, y=%.3f | planned_dist=%.6f m",
                target_x, target_y, target_th, str(target_map_id), init_x, init_y, planned_dist
            )
            logger.info("move_robot_to_product: AGV go_to_pose -> x=%.3f y=%.3f th=%.2f map_id=%s", target_x, target_y, target_th, str(target_map_id))
            await symovo.go_to_pose(
                x_m=target_x,
                y_m=target_y,
                theta_deg=target_th,
                map_id=target_map_id,
                max_speed_m_s=None,
                wait=False,
            )
            await asyncio.sleep(0)

            percent_tol = 0.1
            max_wait_s = 180.0
            interval_s = 2
            waited = 0.0
            while True:
                pose_resp = await symovo.pose()
                pose = (pose_resp.get('pose') or {}) if isinstance(pose_resp, dict) else {}
                cur_x = float(pose.get('x_m') or 0.0)
                cur_y = float(pose.get('y_m') or 0.0)

                err_dist = math.hypot(target_x - cur_x, target_y - cur_y)
                denom_dist = planned_dist if planned_dist > linear_eps else max(1.0, abs(target_x) + abs(target_y))
                rel_linear = err_dist / denom_dist if denom_dist > 0 else 0.0

                logger.info(
                    "AGV current: x=%.3f, y=%.3f | err=%.6f m | rel=%.4f",
                    cur_x, cur_y, err_dist, rel_linear
                )
                if rel_linear <= percent_tol:
                    logger.info(
                        "AGV reached XY tolerance: err=%.6f m, rel=%.4f <= %.4f — proceeding",
                        err_dist, rel_linear, percent_tol
                    )
                    break

                if waited >= max_wait_s:
                    logger.warning(
                        "AGV timeout waiting XY: cur=(%.3f, %.3f), target=(%.3f, %.3f), err=%.6f m",
                        cur_x, cur_y, target_x, target_y, err_dist
                    )
                    raise RuntimeError("AGV did not reach target within timeout")
                await asyncio.sleep(interval_s)
                waited += interval_s
        else:
            logger.info(
                "AGV already at XY target within eps: planned_dist=%.6f m, eps=%.6f — proceeding",
                planned_dist, linear_eps
            )

    # After AGV arrival or if AGV not requested: execute lift/manipulator
    if need_igus:
        target_units = int(params.lift_position_cm * 1000)
        logger.info(
            "LIFT: moving to %d (units) [%.2f cm] at velocity=%.1f%%",
            target_units, float(params.lift_position_cm), float(params.velocity_percent)
        )
        ok = await igus_move_and_check(target_units, params.velocity_percent)
        logger.info("LIFT: move result ok=%s", bool(ok))
        await asyncio.sleep(0)

    if need_xarm:
        _params = xarm_positions.get_XarmMoveWithJointsDictParams_for_move_to_center()
        logger.info("MANIPULATOR: move to center pose before tool offsets")
        logger.info("MANIPULATOR: moving to center pose")
        await manipulator.complex_move_with_joints(_params)
        await asyncio.sleep(0)
        _params = XarmMoveWithToolParams(
            x_offset_mm=params.manipulator_offsets.x_offset_mm,
            y_offset_mm=params.manipulator_offsets.y_offset_mm,
            z_offset_mm=params.manipulator_offsets.z_offset_mm,
            velocity_percent=params.velocity_percent,
            reset_faults=params.reset_faults,
        )
        logger.info(
            "MANIPULATOR: tool offsets dx=%.1f mm, dy=%.1f mm, dz=%.1f mm, v=%.1f%%",
            float(params.manipulator_offsets.x_offset_mm),
            float(params.manipulator_offsets.y_offset_mm),
            float(params.manipulator_offsets.z_offset_mm),
            float(params.velocity_percent),
        )
        await manipulator.change_tool_position(_params)
        logger.info("MANIPULATOR: tool offset move done")
        await asyncio.sleep(0)

    logger.info("move_robot_to_product: done")
    return True

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
            state = await symovo.status()
            return _normalize_symovo_status(state)
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
        isinstance(results[2], SymovoStatusResponse)
        and results[2].online
        and results[2].enabled
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