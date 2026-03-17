"""RobotActor: single owner of XArmAPI, command queue, sequential execution."""
import asyncio
import logging
import time
from typing import Optional, Any

from fastapi.concurrency import run_in_threadpool
from xarm.wrapper import XArmAPI

from app.config import XARM_IP, JOINT_SPEED_DEG_S, JOINT_ACC_DEG_S2, SMART_GRASP_TIMEOUT_S, WS_CHECK_DISABLED
from app.config import GRIPPER_IDLE_TIMEOUT_S, GRIPPER_WATCHDOG_ENABLED
from drivers.xarm_driver.actor.commands import (
    Command,
    CommandResult,
    CommandType,
    ResultStatus,
    ExecutionPolicy,
)
from drivers.xarm_driver.connection.lifecycle import ConnectionLifecycle
from drivers.xarm_driver.picobot_lib import GripperController
import math
from drivers.xarm_driver.safety import (
    apply_boundary_to_arm,
    SafetyEnvelope,
    TrajectoryValidator,
    SafetyMonitor,
    GripperWatchdog,
)
from services.depth_service import DepthService, get_depth_service
from drivers.xarm_driver.usecases.grasp_planner import GraspPlanner
from models.grasp_types import PixelCoord, GraspOutcome

logger = logging.getLogger(__name__)


class RobotActor:
    """
    Single owner of XArmAPI. Runs connect_loop and executor_loop.
    Commands are executed sequentially in a threadpool.
    """

    def __init__(
        self,
        state_store_getter,
        ip: str = None,
    ):
        self._get_store = state_store_getter
        self._ip = ip or XARM_IP
        self._arm: Optional[XArmAPI] = None
        self._lifecycle: Optional[ConnectionLifecycle] = None
        self._queue: asyncio.Queue[Command] = asyncio.Queue()
        self._pending_futures: dict = {}  # command_id -> Future[CommandResult]
        self._current_command_id: Optional[str] = None
        self._tasks: list = []
        self._stopping = False
        self._safety_monitor: Optional[SafetyMonitor] = None
        self._gripper_watchdog: Optional[GripperWatchdog] = None
        self._gripper: Optional[GripperController] = None
        self._grasp_planner: Optional[GraspPlanner] = None

    def get_arm(self) -> Optional[XArmAPI]:
        """Return the XArmAPI instance when connected. None otherwise."""
        return self._arm if (self._arm and getattr(self._arm, "connected", False)) else None

    async def start(self) -> None:
        """Start connect_loop, executor_loop, and SafetyMonitor."""
        if self._tasks:
            return
        self._stopping = False
        store = self._get_store()
        self._lifecycle = ConnectionLifecycle(lambda: store)
        async def _request_stop():
            await self.enqueue(Command("stop-safety", CommandType.STOP, {}, policy=ExecutionPolicy.QUEUE))

        def _get_joints():
            a = self.get_arm()
            return a.get_servo_angle() if a else (1, [])

        def _get_fk(angles):
            a = self.get_arm()
            return self._get_forward_kinematics_compat(a, angles) if a else (1, [])

        self._safety_monitor = SafetyMonitor(
            get_joints=_get_joints,
            get_fk=_get_fk,
            state_store_getter=self._get_store,
            rate_hz=30.0,
            stop_callback=_request_stop,
        )

        # ── Gripper idle-vacuum watchdog ─────────────────────────────────
        if GRIPPER_WATCHDOG_ENABLED and GRIPPER_IDLE_TIMEOUT_S > 0:
            async def _release_gripper():
                await self.enqueue(
                    Command("watchdog-grip-open", CommandType.GRIP_OPEN, {},
                            policy=ExecutionPolicy.QUEUE)
                )

            def _read_vacuum():
                return self._read_vacuum_state()

            self._gripper_watchdog = GripperWatchdog(
                state_store_getter=self._get_store,
                vacuum_reader=_read_vacuum,
                release_callback=_release_gripper,
                timeout_s=GRIPPER_IDLE_TIMEOUT_S,
                rate_hz=1.0,
            )
        else:
            logger.info("GripperWatchdog disabled (enabled=%s, timeout=%.0f)",
                        GRIPPER_WATCHDOG_ENABLED, GRIPPER_IDLE_TIMEOUT_S)

        t1 = asyncio.create_task(self._connect_loop())
        t2 = asyncio.create_task(self._executor_loop())
        t3 = asyncio.create_task(self._safety_monitor.start())
        if self._gripper_watchdog:
            t4 = asyncio.create_task(self._gripper_watchdog.start())
            self._tasks = [t1, t2, t3, t4]
        else:
            self._tasks = [t1, t2, t3]
        logger.info("RobotActor started")

    async def stop(self) -> None:
        """Stop loops, SafetyMonitor, disconnect, release callbacks."""
        self._stopping = True
        if self._gripper_watchdog:
            await self._gripper_watchdog.stop()
            self._gripper_watchdog = None
        if self._safety_monitor:
            await self._safety_monitor.stop()
            self._safety_monitor = None
        if self._lifecycle:
            self._lifecycle.release()
            self._lifecycle = None
        if self._arm:
            try:
                self._arm.disconnect()
            except Exception as e:
                logger.warning("Disconnect: %s", e)
            self._arm = None
        self._get_store().set_connected(False)
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []
        logger.info("RobotActor stopped")

    async def _connect_loop(self) -> None:
        """Connect to robot, register callbacks, apply boundary. Reconnect with backoff on failure."""
        store = self._get_store()
        backoff = 1.0
        reconnect_count = 0
        while not self._stopping:
            try:
                host = self._ip
                try:
                    import socket
                    host = socket.gethostbyname(host)
                except Exception:
                    pass
                logger.info("Connecting to %s", host)
                arm = XArmAPI(host)
                arm.get_robot_sn()
                self._arm = arm
                store.set_connected(True)
                store.set_last_seen(time.time())
                reconnect_count += 1
                try:
                    store.set_reconnect_count(reconnect_count)
                except Exception:
                    pass
                if self._lifecycle:
                    self._lifecycle.register(arm)
                if WS_CHECK_DISABLED:
                    logger.warning("Workspace safety checks are DISABLED via WS_CHECK_DISABLED")
                else:
                    try:
                        apply_boundary_to_arm(arm)
                    except Exception as e:
                        logger.warning("apply_boundary_to_arm: %s", e)
                # Init gripper controller (best-effort)
                try:
                    self._gripper = GripperController(arm, baudrate=115200, timeout=100)
                except Exception as e:
                    self._gripper = None
                    logger.warning("GripperController init: %s", e)
                # On (re)connect, start in conservative state: motion disabled until explicitly enabled
                try:
                    store.set_robot(motion_enabled=False)
                except Exception:
                    pass
                backoff = 1.0
                while not self._stopping and getattr(arm, "connected", False):
                    await asyncio.sleep(1.0)
                # When arm.connected flips False, ensure lifecycle/state are updated
                try:
                    arm.disconnect()
                except Exception:
                    pass
                self._arm = None
                if self._lifecycle:
                    self._lifecycle.release()
                store.set_connected(False)
            except Exception as e:
                logger.warning("Connect loop error: %s", e)
                self._arm = None
                if self._lifecycle:
                    self._lifecycle.release()
                store.set_connected(False)
            if not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)

    def _try_reinit_gripper(self) -> bool:
        """Attempt to (re-)initialise GripperController after a fault clear.

        Returns True if self._gripper is usable after the call.
        """
        if self._gripper is not None:
            return True
        arm = self.get_arm()
        if not arm:
            return False
        try:
            # clean errors first — modbus init often fails while C22 is active
            arm.clean_error()
            arm.set_state(0)
        except Exception:
            pass
        try:
            self._gripper = GripperController(arm, baudrate=115200, timeout=100)
            logger.info("GripperController re-init succeeded")
            return True
        except Exception as e:
            self._gripper = None
            logger.warning("GripperController re-init failed: %s", e)
            return False

    async def _executor_loop(self) -> None:
        """Single consumer: get command from queue, execute, publish result."""
        store = self._get_store()
        while not self._stopping:
            try:
                command = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            started = time.time()
            store.set_busy(True, command.command_id)
            self._current_command_id = command.command_id
            result = await self._execute_command(command)
            result.started_at = started
            result.finished_at = time.time()
            store.set_busy(False)
            store.set_last_result(result)
            self._current_command_id = None
            fut = self._pending_futures.pop(command.command_id, None)
            if fut is not None and not fut.done():
                fut.set_result(result)

    def _execute_move_joints(self, arm: Any, command: Command) -> CommandResult:
        """Execute MOVE_JOINTS with preflight and trajectory validation."""
        params = command.params
        angles = [
            params.get("j1", 0), params.get("j2", 0), params.get("j3", 0),
            params.get("j4", 0), params.get("j5", 0), params.get("j6", 0),
        ]
        angles_7 = (list(angles) + [0.0] * 7)[:7]
        code_fk, pose = self._get_forward_kinematics_compat(arm, angles_7)
        if code_fk == 0 and pose and len(pose) >= 3:
            envelope = SafetyEnvelope()
            check = envelope.check_tcp_in_ws(pose)
            if not check.ok:
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.FAILED,
                    error_message="OUT_OF_WORKSPACE",
                )
        code_cur, current_joints = arm.get_servo_angle()
        if code_cur == 0 and current_joints and len(current_joints) >= 6:
            validator = TrajectoryValidator(num_points=20)
            cur_6 = list(current_joints)[:6]
            end_6 = list(angles)[:6]
            def get_fk(j):
                j7 = (j + [0.0] * 7)[:7]
                return self._get_forward_kinematics_compat(arm, j7)
            traj_check = validator.validate_trajectory(cur_6, end_6, get_fk)
            if not traj_check.ok:
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.FAILED,
                    error_message="OUT_OF_WORKSPACE",
                )
        speed_pct = int(params.get("velocity_percent", 20))
        speed = min(JOINT_SPEED_DEG_S, max(1, (speed_pct / 100.0) * JOINT_SPEED_DEG_S))
        acc = min(JOINT_ACC_DEG_S2, max(1, (speed_pct / 100.0) * JOINT_ACC_DEG_S2))
        code = arm.set_servo_angle(
            angle=angles, speed=speed, mvacc=acc, wait=True, radius=-1.0
        )
        if code != 0:
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_code=code,
                error_message=f"set_servo_angle code={code}",
            )
        return CommandResult(command_id=command.command_id, status=ResultStatus.SUCCEEDED)

    @staticmethod
    def _get_forward_kinematics_compat(arm: Any, joints_7: list[float]) -> tuple[int, Any]:
        """Call SDK FK across xArm SDK versions with varying argument names."""
        try:
            return arm.get_forward_kinematics(joints_7, input_is_radian=False, return_is_radian=False)
        except TypeError:
            try:
                return arm.get_forward_kinematics(joints_7, is_radian=False)
            except TypeError:
                return arm.get_forward_kinematics(joints_7)

    def _execute_command_sync(self, command: Command) -> CommandResult:
        """Execute one command synchronously (run in threadpool)."""
        arm = self.get_arm()
        if not arm:
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message="Not connected",
            )
        try:
            if command.type == CommandType.GET_STATUS:
                code, pos = arm.get_position()
                code2, angles = arm.get_servo_angle()
                state_raw = arm.get_state()
                state = state_raw[1] if isinstance(state_raw, (list, tuple)) and len(state_raw) > 1 else state_raw
                err_warn = arm.get_err_warn_code()
                snapshot = {
                    "position": list(pos) if code == 0 else None,
                    "angles": list(angles) if code2 == 0 else None,
                    "state": state,
                    "error_code": err_warn[0] if isinstance(err_warn, (list, tuple)) else err_warn,
                    "warn_code": err_warn[1] if isinstance(err_warn, (list, tuple)) and len(err_warn) > 1 else 0,
                }
                try:
                    self._get_store().set_robot(
                        mode=int(state or 0),
                        error_code=int(snapshot.get("error_code") or 0),
                        warn_code=int(snapshot.get("warn_code") or 0),
                        joint_angles=snapshot.get("angles"),
                        tcp_pose=snapshot.get("position"),
                    )
                except Exception:
                    pass
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED,
                    telemetry_snapshot=snapshot,
                )
            if command.type == CommandType.MOVE_JOINTS:
                return self._execute_move_joints(arm, command)

            if command.type == CommandType.MOVE_POSE:
                # Pose name resolves to joints from xarm_positions. Executed as joint move with the same safety checks.
                from drivers.xarm_driver.xarm_positions import poses
                name = str(command.params.get("name", "") or "")
                pose = next((p for p in poses if p.get("name") == name), None)
                if not pose or "joints" not in pose:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message=f"Unknown pose name: {name}",
                    )
                j = pose["joints"]
                cmd2 = Command(
                    command_id=command.command_id,
                    type=CommandType.MOVE_JOINTS,
                    params={
                        "j1": float(j.get("j1", 0)),
                        "j2": float(j.get("j2", 0)),
                        "j3": float(j.get("j3", 0)),
                        "j4": float(j.get("j4", 0)),
                        "j5": float(j.get("j5", 0)),
                        "j6": float(j.get("j6", 0)),
                        "velocity_percent": float(command.params.get("velocity_percent", 20)),
                    },
                    policy=command.policy,
                    timeout_s=command.timeout_s,
                )
                return self._execute_move_joints(arm, cmd2)

            if command.type == CommandType.MOVE_TOOL_POSITION:
                # Relative move in tool frame (mm). Preflight: transform tool offset to base using current RPY.
                params = command.params
                x_off = float(params.get("x_offset_mm", 0.0))
                y_off = float(params.get("y_offset_mm", 0.0))
                z_off = float(params.get("z_offset_mm", 0.0))
                # current TCP pose in base frame
                code_pos, cur = arm.get_position()
                if code_pos != 0 or not cur or len(cur) < 6:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message="Failed to read current pose",
                    )
                cx, cy, cz, roll, pitch, yaw = [float(cur[i]) for i in range(6)]
                # R = Rz(yaw)*Ry(pitch)*Rx(roll) (deg)
                r = math.radians(roll)
                p = math.radians(pitch)
                y = math.radians(yaw)
                cr, sr = math.cos(r), math.sin(r)
                cp, sp = math.cos(p), math.sin(p)
                cyaw, syaw = math.cos(y), math.sin(y)
                # rotation matrix
                R = [
                    [cyaw * cp, cyaw * sp * sr - syaw * cr, cyaw * sp * cr + syaw * sr],
                    [syaw * cp, syaw * sp * sr + cyaw * cr, syaw * sp * cr - cyaw * sr],
                    [-sp,       cp * sr,                  cp * cr],
                ]
                dx = R[0][0] * x_off + R[0][1] * y_off + R[0][2] * z_off
                dy = R[1][0] * x_off + R[1][1] * y_off + R[1][2] * z_off
                dz = R[2][0] * x_off + R[2][1] * y_off + R[2][2] * z_off
                end_pose = [cx + dx, cy + dy, cz + dz]
                envelope = SafetyEnvelope()
                check = envelope.check_tcp_in_ws(end_pose)
                if not check.ok:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message="OUT_OF_WORKSPACE",
                    )
                speed_pct = float(params.get("velocity_percent", 20))
                from app.config import TCP_SPEED_MM_S, TCP_ACC_MM_S2
                speed = max(1.0, min(TCP_SPEED_MM_S, (speed_pct / 100.0) * TCP_SPEED_MM_S))
                acc = max(1.0, min(TCP_ACC_MM_S2, (speed_pct / 100.0) * TCP_ACC_MM_S2))
                roll_off = float(params.get("roll_offset_deg", 0.0))
                pitch_off = float(params.get("pitch_offset_deg", 0.0))
                yaw_off = float(params.get("yaw_offset_deg", 0.0))
                code = arm.set_tool_position(
                    x=int(x_off), y=int(y_off), z=int(z_off),
                    roll=roll_off, pitch=pitch_off, yaw=yaw_off,
                    radius=0, speed=speed, mvacc=acc,
                    relative=True, wait=True
                )
                if code != 0:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_code=code,
                        error_message=f"set_tool_position code={code}",
                    )
                return CommandResult(command_id=command.command_id, status=ResultStatus.SUCCEEDED)

            if command.type == CommandType.CHECK_IK:
                # IK feasibility check — purely computational, no motion
                pose = command.params.get("pose")  # [x, y, z, roll, pitch, yaw] in mm/degrees
                if not pose or len(pose) != 6:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message="CHECK_IK requires pose=[x,y,z,roll,pitch,yaw]",
                    )
                code, angles = arm.get_inverse_kinematics(
                    pose, input_is_radian=False, return_is_radian=False,
                )
                feasible = code == 0 and len(angles) > 0
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED,
                    telemetry_snapshot={
                        "feasible": feasible,
                        "ik_code": code,
                        "joint_angles": list(angles) if feasible else [],
                    },
                )

            if command.type == CommandType.GRIP_CLOSE:
                if not self._gripper:
                    self._try_reinit_gripper()
                if not self._gripper:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message="Gripper controller not available",
                    )
                code = self._gripper.activate()
                ok = bool(code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220])
                try:
                    self._get_store().set_robot(gripper_active=ok)
                except Exception:
                    pass
                # Reset watchdog so it starts a fresh countdown
                if ok and self._gripper_watchdog:
                    self._gripper_watchdog.reset()
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED if ok else ResultStatus.FAILED,
                    error_message=None if ok else "gripper_activate_failed",
                )

            if command.type == CommandType.GRIP_OPEN:
                if not self._gripper:
                    self._try_reinit_gripper()
                if not self._gripper:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_message="Gripper controller not available",
                    )
                code = self._gripper.deactivate()
                ok = bool(code == [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220])
                try:
                    self._get_store().set_robot(gripper_active=not ok)
                except Exception:
                    pass
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED if ok else ResultStatus.FAILED,
                    error_message=None if ok else "gripper_deactivate_failed",
                )

            if command.type == CommandType.STOP:
                try:
                    arm.stop()
                except Exception:
                    pass
                return CommandResult(command_id=command.command_id, status=ResultStatus.SUCCEEDED)

            if command.type == CommandType.RECOVER_FAULTS:
                # Explicit recovery ONLY; does not enable motion.
                try:
                    arm.clean_warn()
                except Exception:
                    pass
                try:
                    arm.clean_error()
                except Exception:
                    pass
                try:
                    arm.set_state(0)
                except Exception:
                    pass
                # Clear fault flags in store
                try:
                    self._get_store().set_robot(error_code=0, warn_code=0)
                except Exception:
                    pass
                # Re-init gripper if it was lost due to modbus/controller errors
                if not self._gripper:
                    self._try_reinit_gripper()
                return CommandResult(command_id=command.command_id, status=ResultStatus.SUCCEEDED)

            if command.type == CommandType.ENABLE_MOTION:
                code = arm.motion_enable(True)
                if code == 0:
                    code2 = arm.set_mode(0)
                    code3 = arm.set_state(0)
                    logger.info("ENABLE_MOTION: motion_enable=%d set_mode=%d set_state=%d", code, code2, code3)
                else:
                    logger.warning("ENABLE_MOTION: motion_enable failed code=%d", code)
                try:
                    self._get_store().set_robot(motion_enabled=(code == 0))
                except Exception:
                    pass
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED if code == 0 else ResultStatus.FAILED,
                    error_code=code if code != 0 else None,
                )

            if command.type == CommandType.DISABLE_MOTION:
                code = arm.motion_enable(False)
                try:
                    self._get_store().set_robot(motion_enabled=False)
                except Exception:
                    pass
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED if code == 0 else ResultStatus.FAILED,
                    error_code=code if code != 0 else None,
                )

            if command.type == CommandType.GET_TCP_POSITION:
                code, pos = arm.get_position()
                if code != 0 or not pos or len(pos) < 6:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_code=code,
                        error_message=f"get_position code={code}",
                    )
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED,
                    telemetry_snapshot={"position": list(pos)},
                )

            if command.type == CommandType.SET_TCP_POSITION:
                p = command.params
                from app.config import TCP_SPEED_MM_S, TCP_ACC_MM_S2
                speed_pct = float(p.get("velocity_percent", 20.0))
                speed = max(1.0, min(TCP_SPEED_MM_S, (speed_pct / 100.0) * TCP_SPEED_MM_S))
                acc = max(1.0, min(TCP_ACC_MM_S2, (speed_pct / 100.0) * TCP_ACC_MM_S2))
                # Ensure arm is in ready state (may be in STOP after joint move)
                arm.set_mode(0)
                arm.set_state(0)
                code = arm.set_position(
                    x=float(p["x"]), y=float(p["y"]), z=float(p["z"]),
                    roll=float(p["roll"]), pitch=float(p["pitch"]), yaw=float(p["yaw"]),
                    speed=speed, mvacc=acc, wait=True,
                )
                if code != 0:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.FAILED,
                        error_code=code,
                        error_message=f"set_position code={code}",
                    )
                return CommandResult(command_id=command.command_id, status=ResultStatus.SUCCEEDED)

            if command.type == CommandType.GRIPPER_STATUS:
                if not self._gripper:
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.SUCCEEDED,
                        telemetry_snapshot={
                            "active": False,
                            "feedback": "UNKNOWN",
                            "sensor_supported": False,
                        },
                    )
                try:
                    gs = self._gripper.get_status()
                except Exception as e:
                    logger.warning("GRIPPER_STATUS degraded: %s", e)
                    return CommandResult(
                        command_id=command.command_id,
                        status=ResultStatus.SUCCEEDED,
                        telemetry_snapshot={
                            "active": False,
                            "feedback": "UNKNOWN",
                            "sensor_supported": False,
                            "error": str(e),
                        },
                    )
                snap = self._get_store().get_robot_snapshot()
                return CommandResult(
                    command_id=command.command_id,
                    status=ResultStatus.SUCCEEDED,
                    telemetry_snapshot={
                        "active": gs.active,
                        "feedback": gs.feedback.value,
                        "vacuum_level": gs.vacuum_level,
                        "part_present": gs.part_present,
                        "part_secured": gs.part_secured,
                        "energy_saving": gs.energy_saving,
                        "motor_stall": gs.motor_stall,
                        "pcb_temperature": gs.pcb_temperature,
                        "membrane_hours": gs.membrane_hours,
                        "membrane_warn": gs.membrane_warn,
                        "sensor_supported": gs.sensor_supported,
                        "sensor_disabled_reason": getattr(self._gripper, "pdi_disabled_reason", None),
                        "modbus_raw": gs.modbus_raw,
                        "activated_at": snap.gripper_activated_at,
                        "idle_elapsed_s": round(time.time() - snap.gripper_activated_at, 1)
                            if snap.gripper_activated_at else None,
                        "watchdog_enabled": GRIPPER_WATCHDOG_ENABLED and GRIPPER_IDLE_TIMEOUT_S > 0,
                        "watchdog_timeout_s": GRIPPER_IDLE_TIMEOUT_S,
                    },
                )

            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message=f"Unhandled command type: {command.type}",
            )
        except Exception as e:
            logger.exception("Execute command %s: %s", command.command_id, e)
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message=str(e),
            )

    async def _execute_command(self, command: Command) -> CommandResult:
        # SMART_GRASP is async (orchestrates sub‑commands) — handle outside threadpool
        if command.type == CommandType.SMART_GRASP:
            return await self._execute_smart_grasp(command)
        if command.type == CommandType.DEPTH_SCAN:
            return await self._execute_depth_scan(command)
        return await run_in_threadpool(self._execute_command_sync, command)

    # ── SMART_GRASP ────────────────────────────────────────────────────────

    def _ensure_grasp_planner(self) -> GraspPlanner:
        if self._grasp_planner is None:
            depth = get_depth_service()
            self._grasp_planner = GraspPlanner(
                actor_enqueue=self._execute_inline,  # bypass queue to avoid deadlock
                depth_service=depth,
                gripper_status_fn=self._read_vacuum_state,
                torque_fn=self._read_torques,
                get_joints_fn=self._read_joints,
            )
        return self._grasp_planner

    def _read_vacuum_state(self) -> int:
        """Return vacuum state: 1=part_secured, 0=vacuum_on_no_part, -1=off, -99=error."""
        if not self._gripper:
            return -99
        try:
            pdi = self._gripper.read_pdi()
            if pdi.part_secured:
                return 1
            if pdi.part_present:
                return 0
            return -1
        except Exception:
            return -99

    def _read_torques(self):
        return self._gripper.read_joints_torque() if self._gripper else None

    def _read_joints(self) -> Optional[dict]:
        """Return current joint angles as {j1..j6} dict, or None."""
        arm = self.get_arm()
        if not arm:
            return None
        code, angles = arm.get_servo_angle()
        if code != 0 or not angles or len(angles) < 6:
            return None
        return {
            "j1": float(angles[0]),
            "j2": float(angles[1]),
            "j3": float(angles[2]),
            "j4": float(angles[3]),
            "j5": float(angles[4]),
            "j6": float(angles[5]),
        }

    async def _execute_inline(self, command: Command) -> "CommandResult":
        """Execute a command *directly* (bypass the queue).

        Used by SMART_GRASP sub-commands to avoid deadlocking the
        single-consumer executor loop.
        """
        return await run_in_threadpool(self._execute_command_sync, command)

    async def _auto_prepare(self) -> None:
        """Automatically recover faults and enable motion before smart grasp."""
        recover_cmd = Command(
            command_id=f"auto-recover-{time.monotonic_ns()}",
            type=CommandType.RECOVER_FAULTS,
            params={},
            policy=ExecutionPolicy.QUEUE,
        )
        r1 = await self._execute_inline(recover_cmd)
        logger.info("Auto-prepare recover: %s %s", r1.status, r1.error_message or '')

        enable_cmd = Command(
            command_id=f"auto-enable-{time.monotonic_ns()}",
            type=CommandType.ENABLE_MOTION,
            params={},
            policy=ExecutionPolicy.QUEUE,
        )
        r2 = await self._execute_inline(enable_cmd)
        logger.info("Auto-prepare enable: %s %s", r2.status, r2.error_message or '')

    async def _execute_smart_grasp(self, command: Command) -> CommandResult:
        try:
            return await asyncio.wait_for(
                self._execute_smart_grasp_inner(command),
                timeout=SMART_GRASP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("SMART_GRASP global timeout (%.0fs)", SMART_GRASP_TIMEOUT_S)
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message=f"Smart grasp timed out after {SMART_GRASP_TIMEOUT_S}s",
            )
        except Exception as e:
            logger.exception("SMART_GRASP error: %s", e)
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message=str(e),
            )

    async def _execute_smart_grasp_inner(self, command: Command) -> CommandResult:
        # Auto-prepare: recover faults and enable motion if needed
        logger.info("SMART_GRASP: starting auto-prepare")
        await self._auto_prepare()
        logger.info("SMART_GRASP: auto-prepare done, starting planner")

        planner = self._ensure_grasp_planner()
        params = command.params
        target = None
        if "target_x" in params and "target_y" in params:
            target = PixelCoord(int(params["target_x"]), int(params["target_y"]))
        result = await planner.execute_grasp(target_px=target)
        logger.info("SMART_GRASP: outcome=%s attempts=%d elapsed=%.1fs",
                     result.outcome.value, result.attempts, result.elapsed_s)
        snapshot = {
            "outcome": result.outcome.value,
            "attempts": result.attempts,
            "elapsed_s": round(result.elapsed_s, 2),
            "error_message": result.error_message,
        }
        if result.detection:
            snapshot["detection"] = {
                "center_px": [result.detection.center_px.x, result.detection.center_px.y],
                "depth_mm": round(result.detection.depth_mm, 1),
                "size_mm": [round(s, 1) for s in result.detection.size_mm],
            }
        if result.verification:
            snapshot["verification"] = {
                "object_held": result.verification.object_held,
                "detail": result.verification.detail,
            }
        return CommandResult(
            command_id=command.command_id,
            status=ResultStatus.SUCCEEDED if result.outcome == GraspOutcome.SUCCESS else ResultStatus.FAILED,
            error_message=result.error_message,
            telemetry_snapshot=snapshot,
        )

    # ── DEPTH_SCAN ─────────────────────────────────────────────────────────

    async def _execute_depth_scan(self, command: Command) -> CommandResult:
        try:
            depth = get_depth_service()
            frame = await asyncio.wait_for(
                depth.get_depth_frame(),
                timeout=SMART_GRASP_TIMEOUT_S,
            )
            detections = depth.detect_objects(frame)
            objects = []
            for d in detections:
                objects.append({
                    "center_px": [d.center_px.x, d.center_px.y],
                    "bbox": list(d.bbox),
                    "depth_mm": round(d.depth_mm, 1),
                    "size_mm": [round(s, 1) for s in d.size_mm],
                    "contour_area_px": int(d.contour_area_px),
                })
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.SUCCEEDED,
                telemetry_snapshot={"objects": objects, "count": len(objects)},
            )
        except Exception as e:
            logger.exception("DEPTH_SCAN error: %s", e)
            return CommandResult(
                command_id=command.command_id,
                status=ResultStatus.FAILED,
                error_message=str(e),
            )

    async def enqueue(self, command: Command) -> CommandResult:
        """Enqueue command and wait for result."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_futures[command.command_id] = future
        await self._queue.put(command)
        try:
            return await future
        finally:
            self._pending_futures.pop(command.command_id, None)
