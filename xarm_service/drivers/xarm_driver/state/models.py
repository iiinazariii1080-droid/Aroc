"""State models for StateStore: connection, robot, execution."""
from dataclasses import dataclass, field
from typing import Optional, List, Any
import time


@dataclass
class ConnectionState:
    """Connection to robot controller."""
    connected: bool = False
    last_seen: float = 0.0
    reconnect_count: int = 0


@dataclass
class RobotState:
    """Robot motion and fault state."""
    error_code: int = 0
    warn_code: int = 0
    mode: int = 0
    is_moving: bool = False
    joint_angles: Optional[List[float]] = None
    tcp_pose: Optional[List[float]] = None
    gripper_active: bool = False
    gripper_activated_at: Optional[float] = None  # epoch when vacuum was turned ON
    motion_enabled: bool = False


@dataclass
class ExecutionState:
    """Active command execution."""
    active_command_id: Optional[str] = None
    busy: bool = False
    last_result: Optional[Any] = None
