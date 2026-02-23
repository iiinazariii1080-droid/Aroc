"""StateStore: thread-safe central state for connection, robot, execution."""
import asyncio
import threading
import time
from typing import Optional, Any
from .models import ConnectionState, RobotState, ExecutionState


class StateStore:
    """Single source of truth for robot state. Async-safe getters/setters."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connection = ConnectionState()
        self._robot = RobotState()
        self._execution = ExecutionState()
        self._ready_event: Optional[asyncio.Event] = None

    def _sync(self, fn):
        with self._lock:
            return fn()

    # --- Connection ---
    @property
    def connected(self) -> bool:
        return self._sync(lambda: self._connection.connected)

    def set_connected(self, value: bool) -> None:
        def _():
            self._connection.connected = value
            self._connection.last_seen = time.time() if value else 0.0
        self._sync(_)

    def set_last_seen(self, t: float) -> None:
        self._sync(lambda: setattr(self._connection, "last_seen", t))

    def set_reconnect_count(self, n: int) -> None:
        self._sync(lambda: setattr(self._connection, "reconnect_count", n))

    def get_connection(self) -> ConnectionState:
        return self._sync(lambda: ConnectionState(
            connected=self._connection.connected,
            last_seen=self._connection.last_seen,
            reconnect_count=self._connection.reconnect_count,
        ))

    # --- Robot ---
    @property
    def faulted(self) -> bool:
        return self._sync(lambda: self._robot.error_code != 0)

    @property
    def motion_enabled(self) -> bool:
        return self._sync(lambda: self._robot.motion_enabled)

    @property
    def busy(self) -> bool:
        return self._sync(lambda: self._execution.busy or self._robot.is_moving)

    def set_robot(
        self,
        error_code: Optional[int] = None,
        warn_code: Optional[int] = None,
        mode: Optional[int] = None,
        is_moving: Optional[bool] = None,
        joint_angles: Optional[list] = None,
        tcp_pose: Optional[list] = None,
        gripper_active: Optional[bool] = None,
        motion_enabled: Optional[bool] = None,
    ) -> None:
        def _():
            r = self._robot
            if error_code is not None:
                r.error_code = error_code
            if warn_code is not None:
                r.warn_code = warn_code
            if mode is not None:
                r.mode = mode
            if is_moving is not None:
                r.is_moving = is_moving
            if joint_angles is not None:
                r.joint_angles = list(joint_angles)
            if tcp_pose is not None:
                r.tcp_pose = list(tcp_pose)
            if gripper_active is not None:
                r.gripper_active = gripper_active
                # Auto-manage activation timestamp
                if gripper_active:
                    if r.gripper_activated_at is None:
                        r.gripper_activated_at = time.time()
                else:
                    r.gripper_activated_at = None
            if motion_enabled is not None:
                r.motion_enabled = motion_enabled
        self._sync(_)

    def get_robot_snapshot(self) -> RobotState:
        return self._sync(lambda: RobotState(
            error_code=self._robot.error_code,
            warn_code=self._robot.warn_code,
            mode=self._robot.mode,
            is_moving=self._robot.is_moving,
            joint_angles=self._robot.joint_angles.copy() if self._robot.joint_angles else None,
            tcp_pose=self._robot.tcp_pose.copy() if self._robot.tcp_pose else None,
            gripper_active=self._robot.gripper_active,
            gripper_activated_at=self._robot.gripper_activated_at,
            motion_enabled=self._robot.motion_enabled,
        ))

    def refresh_gripper_activated_at(self) -> None:
        """Force-reset gripper_activated_at to now (extends watchdog timer)."""
        self._sync(lambda: setattr(self._robot, "gripper_activated_at", time.time()))

    # --- Execution ---
    def set_busy(self, busy: bool, command_id: Optional[str] = None) -> None:
        def _():
            self._execution.busy = busy
            self._execution.active_command_id = command_id if busy else None
        self._sync(_)

    def set_last_result(self, result: Any) -> None:
        self._sync(lambda: setattr(self._execution, "last_result", result))

    def get_execution(self) -> ExecutionState:
        return self._sync(lambda: ExecutionState(
            active_command_id=self._execution.active_command_id,
            busy=self._execution.busy,
            last_result=self._execution.last_result,
        ))
