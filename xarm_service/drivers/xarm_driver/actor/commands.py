"""Command and CommandResult models for RobotActor."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
import time


class CommandType(str, Enum):
    GET_STATUS = "GET_STATUS"
    MOVE_JOINTS = "MOVE_JOINTS"
    MOVE_POSE = "MOVE_POSE"
    MOVE_LINEAR = "MOVE_LINEAR"
    MOVE_TOOL_POSITION = "MOVE_TOOL_POSITION"
    GRIP_OPEN = "GRIP_OPEN"
    GRIP_CLOSE = "GRIP_CLOSE"
    SMART_GRASP = "SMART_GRASP"
    DEPTH_SCAN = "DEPTH_SCAN"
    GRIPPER_STATUS = "GRIPPER_STATUS"
    CHECK_IK = "CHECK_IK"
    STOP = "STOP"
    RECOVER_FAULTS = "RECOVER_FAULTS"
    ENABLE_MOTION = "ENABLE_MOTION"
    DISABLE_MOTION = "DISABLE_MOTION"
    GET_TCP_POSITION = "GET_TCP_POSITION"
    SET_TCP_POSITION = "SET_TCP_POSITION"


class ExecutionPolicy(str, Enum):
    REJECT_IF_BUSY = "REJECT_IF_BUSY"
    QUEUE = "QUEUE"
    PREEMPT_ACTIVE = "PREEMPT_ACTIVE"


class ResultStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass
class Command:
    command_id: str
    type: CommandType
    params: Dict[str, Any]
    timeout_s: float = 60.0
    policy: ExecutionPolicy = ExecutionPolicy.REJECT_IF_BUSY


@dataclass
class CommandResult:
    command_id: str
    status: ResultStatus
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    telemetry_snapshot: Optional[Dict[str, Any]] = None
