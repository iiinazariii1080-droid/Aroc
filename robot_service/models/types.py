from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Annotated, List



class JointLimits:
    DEFAULT = 0.0
    MIN = -500.0
    MAX = 500.0

class VelocityLimits:
    DEFAULT = 100.0
    MIN = 0.1
    MAX = 100.0

class PercentLimits:
    DEFAULT = 100.0
    MIN = 0.1
    MAX = 100.0

class OffsetLimits:
    DEFAULT = 0.0
    MIN = -1000.0
    MAX = 1000.0

class Parameters:
    joint = Annotated[float,Field(description="joint angle in degrees",),]
    velocity_percent = Annotated[float,Field(description="Value in percent of the drive's maximum",),]
    reset_faults = Annotated[bool,Field(description="If true, API resets faults"),]
    offset_mm = Annotated[float,Field(description="Offset in millimeters",),]
    alive = Annotated[bool,Field(description="True if the robot is alive"),]
    connected = Annotated[bool,Field(description="True if the robot is connected"),]
    state_code = Annotated[int,Field(description="Robot state code"),]
    has_err_warn = Annotated[bool,Field(description="True if the robot has errors or warnings"),]
    has_error = Annotated[bool,Field(description="True if the robot has errors"),]
    has_warn = Annotated[bool,Field(description="True if the robot has warnings"),]
    error_code = Annotated[int,Field(description="Robot error code"),]

class JoystickCommand(BaseModel):
    axes: List[float]
    buttons: List[int]

class JoystickFrame(BaseModel):
    ts: float
    axes: List[float]
    buttons: List[int]
    ttl: int
    
class Joints(BaseModel):
    j1: Parameters.joint
    j2: Parameters.joint
    j3: Parameters.joint
    j4: Parameters.joint
    j5: Parameters.joint
    j6: Parameters.joint

class RobotActionResponse(BaseModel):
    velocity_percent: Parameters.velocity_percent

class MoveWithJointsParams(BaseModel):
    joints: Joints
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults

class MoveWithJointsDictParams(BaseModel):
    points: List[Joints]
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults

class MoveWithPoseParams(BaseModel):
    name: str
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults

class MoveWithToolParams(BaseModel):
    x_offset_mm: Parameters.offset_mm
    y_offset_mm: Parameters.offset_mm
    z_offset_mm: Parameters.offset_mm
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults
    roll_offset_deg: Optional[float] = Field(None, description="Roll offset in degrees")
    pitch_offset_deg: Optional[float] = Field(None, description="Pitch offset in degrees")
    yaw_offset_deg: Optional[float] = Field(None, description="Yaw offset in degrees")

class ActionResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    message: Optional[str] = None

class XarmJointsPositionResponse(BaseModel):
    name: str
    joints: Joints

class XarmStatusResponse(BaseModel):
    alive: Parameters.alive
    connected: Parameters.connected
    state_code: Parameters.state_code
    has_err_warn: Parameters.has_err_warn
    has_error: Parameters.has_error
    has_warn: Parameters.has_warn
    error_code: Parameters.error_code

class XarmMoveWithJointsDictParams(BaseModel):
    points: List[Joints]
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults

class XarmMoveWithJointsParams(BaseModel):
    j1: Parameters.joint
    j2: Parameters.joint
    j3: Parameters.joint
    j4: Parameters.joint
    j5: Parameters.joint
    j6: Parameters.joint
    velocity_percent: Parameters.velocity_percent
    reset_faults: Parameters.reset_faults

# Unified aliases for consistent naming across layers
XarmMoveWithPoseParams = MoveWithPoseParams
XarmMoveWithToolParams = MoveWithToolParams
XarmJointsDict = Joints

class DepthQueryRequest(BaseModel):
    x: int
    y: int
