"""
api_types.py - Types for API requests and responses

This module contains all Pydantic models for the API.
Uses base types from base_types.py for standardization.
"""
from typing import Annotated
from typing import Optional, List, Any, Union
from pydantic import Field, validator, StrictBool, StrictFloat 
from models.base_types import (
    # Base types
    JsonDict, OptionalStr, OptionalInt, OptionalFloat, OptionalBool, OptionalDict,
    StringDict, FloatDict, BoolDict, StringList, FloatList, JsonList,
    
    # Constants
    VelocityLimits, PositionLimits, ToolOffsetLimits, JointLimits, CoordinateLimits,
    
    # Enums
    TaskStatus, DeviceState, MovementType, ErrorLevel,
    
    # Base models
    BaseApiModel, BaseResponse, BaseError, BaseAsyncResponse,
    
    # Specialized types
    VelocityPercent, PositionCm, ToolOffsetMm, JointAngleDeg,
)

# ============================================================================
# COORDINATES AND OFFSETS
# ============================================================================

class ProductLocation(BaseApiModel):
    """Target location on AGV map (all units in mm/rad)."""
    x_m: float = Field(..., description="X coordinate in meters", example=0.0)
    y_m: float = Field(..., description="Y coordinate in meters", example=0.0)
    theta_deg: float = Field(0.0, description="Orientation", example=0.0)
    map_id: int = Field(..., description="map code", example=0)

class XarmToolOffsets(BaseApiModel):
    """Offsets for xArm tool (all in mm)."""
    x_offset_mm: float = Field(..., description="Tool X offset in mm", example=0)
    y_offset_mm: float = Field(..., description="Tool Y offset in mm", example=0)
    z_offset_mm: float = Field(..., description="Tool Z offset in mm", example=0)
    
    @validator("x_offset_mm", "y_offset_mm", "z_offset_mm")
    @classmethod
    def validate_tool_offset_mm(cls, v):
        if not (ToolOffsetLimits.MIN_MM <= v <= ToolOffsetLimits.MAX_MM):
            raise ValueError(f"Tool offset must be between {ToolOffsetLimits.MIN_MM} and {ToolOffsetLimits.MAX_MM} mm")
        return v

# ============================================================================
# REQUESTS
# ============================================================================

class DefaultMoveRequest(BaseApiModel):
    """Minimal request for movement (velocity only)."""
    velocity_percent: float = Field(..., description="Speed (%)", example=20, ge=1.0, le=100.0)

    @validator("velocity_percent")
    @classmethod
    def validate_velocity_percent(cls, v):
        if not (VelocityLimits.MIN_PERCENT <= v <= VelocityLimits.MAX_PERCENT):
            raise ValueError(f"Velocity percent must be between {VelocityLimits.MIN_PERCENT} and {VelocityLimits.MAX_PERCENT}")
        return v

    
class DepthQueryRequest(BaseApiModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)

class DepthQueryResponse(BaseApiModel):
    depth: float


class RobotMoveRequest(BaseApiModel):
    """Coordinated movement request."""
    product_id: str = Field(..., description="Product identifier", example="PRODUCT_1", min_length=1)
    location: ProductLocation = Field(..., description="Target location")
    lift_position_cm: OptionalFloat = Field(None, description="Igus lift position (cm)", example=30)
    manipulator_offsets: Optional[XarmToolOffsets] = Field(None, description="xArm tool offsets (mm)")
    velocity_percent: float = Field(..., description="Speed (%)", example=20, ge=1.0, le=100.0)
    reset_faults: bool = Field(False, description="Reset errors and reinitialize before move. Must be boolean true/false.", example=False)

    @validator("velocity_percent")
    @classmethod
    def validate_velocity_percent(cls, v):
        if not (VelocityLimits.MIN_PERCENT <= v <= VelocityLimits.MAX_PERCENT):
            raise ValueError(f"Velocity percent must be between {VelocityLimits.MIN_PERCENT} and {VelocityLimits.MAX_PERCENT}")
        return v


# ============================================================================
# MOVEMENT RESPONSES
# ============================================================================
class SymovoMoveResult(BaseResponse):
    """Result of AGV movement."""
    position_mm: OptionalFloat = Field(None, description="Final AGV position (mm)")
    details: OptionalStr = Field(None, description="Movement details or message")

class IgusMoveResult(BaseResponse):
    """Result of Igus lift movement."""
    position_mm: OptionalFloat = Field(None, description="Final lift position (mm)")
    details: OptionalStr = Field(None, description="Movement details or message")

class XarmMoveResult(BaseResponse):
    """Result of manipulator movement."""
    details: OptionalStr = Field(None, description="Movement details or message")

# ============================================================================
# COORDINATED RESPONSES
# ============================================================================
class RobotActionResponse(BaseApiModel):
    success: bool = Field(..., description="True if command was executed successfully")
    task_id: Optional[str] = Field(None, description="Task id")
    result: Optional[Any] = Field(None, description="Result data, if available")
    detail: Optional[str] = Field(None, description="Human-readable description or message")
    error: Optional[str] = Field(None, description="Error message, if any")

class RobotErrorResponse(BaseApiModel):
    success: bool = False
    error: OptionalStr = Field(None, description="General error or info message")

class RobotMoveResult(BaseApiModel):
    """Result of coordinated robot movement."""
    success: bool = Field(..., description="True if move was executed successfully")
    agv_result: Optional[SymovoMoveResult] = Field(None, description="AGV movement result")
    lift_result: Optional[IgusMoveResult] = Field(None, description="Igus lift movement result")
    manipulator_result: Optional[XarmMoveResult] = Field(None, description="xArm manipulator movement result")
    message: OptionalStr = Field(None, description="General error or info message")

class RobotMoveBoxResult(BaseApiModel):
    """Result of robot movement to box."""
    success: bool = Field(..., description="True if the robot placed product in Box")
    igus_result: Optional[IgusMoveResult] = Field(None, description="Igus lift movement details")
    manipulator_result: Optional[XarmMoveResult] = Field(None, description="xArm manipulator movement details")
    message: OptionalStr = Field(None, description="Additional information or error message")

class RobotTransportPositionResult(BaseApiModel):
    """Result of robot movement to transport position."""
    success: bool = Field(..., description="True if robot moved to transport (stowed) position")
    igus_result: Optional[IgusMoveResult] = Field(None, description="Igus lift movement details (combined intermediate and final moves)")
    manipulator_result: Optional[XarmMoveResult] = Field(None, description="xArm manipulator movement details")
    message: OptionalStr = Field(None, description="Additional information or error message")

# ============================================================================
# ROBOT POSITIONS
# ============================================================================

class RobotPositionLocation(BaseApiModel):
    x_m: float = Field(..., description="X coordinate (meters)", example=1.28)
    y_m: float = Field(..., description="Y coordinate (meters)", example=2.31)
    theta_deg: float = Field(0.0, description="Heading (degrees)", example=-2.0)
    map_id: int = Field(0, description="Map ID", example=0)

class RobotPositionOffsets(BaseApiModel):
    x_offset_mm: float = Field(0.0, description="Tool X offset (mm)", example=0.0)
    y_offset_mm: float = Field(0.0, description="Tool Y offset (mm — left/right)", example=50.0)
    z_offset_mm: float = Field(0.0, description="Tool Z offset (mm)", example=0.0)

class RobotPositionParams(BaseApiModel):
    """Movement parameters stored with each saved position."""
    location: RobotPositionLocation = Field(..., description="AGV target coordinates")
    lift_position_cm: float = Field(0.0, description="Lift height in cm", example=20.0)
    manipulator_offsets: RobotPositionOffsets = Field(
        default_factory=RobotPositionOffsets,
        description="Tool offset from JOB_POSE (left/right = y_offset_mm)"
    )
    velocity_percent: float = Field(20.0, description="Movement speed (%)", example=20.0)
    reset_faults: bool = Field(False, description="Reset device errors before moving")

class RobotPositionItem(BaseApiModel):
    """Single saved robot position."""
    id: str = Field(..., description="Position ID (8-char hex)", example="b16f0fed")
    name: Optional[str] = Field(None, description="Human-readable name", example="Полка А, ряд 3")
    params: Optional[dict] = Field(None, description="Movement parameters")

class RobotPositionRecordRequest(BaseApiModel):
    """Request body for recording current position."""
    name: Optional[str] = Field(None, description="Position name", example="Полка А, ряд 3")
    velocity_percent: float = Field(20.0, description="Speed to use when running this position (%)", example=20.0)

class RobotPositionSaveRequest(BaseApiModel):
    """Request body for manually saving a position."""
    name: Optional[str] = Field(None, description="Position name", example="Полка А, ряд 3")
    params: RobotPositionParams = Field(..., description="Position parameters")

class RobotPositionSavedResponse(BaseApiModel):
    status: str = Field("ok", description="'ok' on success")
    message: str = Field(..., description="Human-readable result")
    id: str = Field(..., description="Saved position ID", example="b16f0fed")

class RobotPositionRecordResponse(RobotPositionSavedResponse):
    snapshot: Optional[dict] = Field(None, description="Raw device readings at time of recording")

class RobotPositionDeleteResponse(BaseApiModel):
    status: str = Field("ok", description="'ok' on success")
    message: str = Field(..., description="Human-readable result")

# ============================================================================
# SUBSYSTEM STATUSES
# ============================================================================

class ErrorStatus(BaseApiModel):
    """Error status with flexible error field and allowed extra inputs."""
    error: Union[JsonDict, bool, str] = Field(..., description="Error details (object/message) or boolean flag")
    class Config:
        extra = "allow"

class IgusStatusResponse(BaseApiModel):
    """Current status of the motor controller."""
    status_word: int = Field(..., description="Status word from the motor controller", example=8192)
    homed: bool = Field(..., description="True if the motor is homed", example=True)
    is_moving: bool = Field(..., description="True if motor is moving", example=False)
    error: bool = Field(..., description="True if there is an error", example=False)
    connected: bool = Field(..., description="True if the motor is connected", example=True)
    position_cm: float = Field(
        PositionLimits.DEFAULT_CM,
        description="Current position in centimeters (0.00–120.00)",
        example=50.0,
        validation_alias="position"
    )

    # Accept raw controller value; range is validated downstream by device layer

class TaskStatusResponse(BaseApiModel):
    """Information about an asynchronous motor task."""
    status: TaskStatus = Field(..., description="Current task status", example=TaskStatus.WORKING)
    result: Optional[JsonDict] = Field(None, description="Result returned when the task completes, if any")

class PositionLimits:
    DEFAULT_CM = 50.0
    MIN_CM = -120.0
    MAX_CM = 120.0

class VelocityLimits:
    DEFAULT_PERCENT = 50.0
    MIN_PERCENT = 1.0
    MAX_PERCENT = 100.0

class IgusMoveParams(BaseApiModel):
    position_cm: Annotated[
        float,
        Field(
            description="Target position in centimeters (0.00–120.00)",
            example=50.0,
            ge=PositionLimits.MIN_CM,
            le=PositionLimits.MAX_CM
        ),
    ] = PositionLimits.DEFAULT_CM

    velocity_percent: Annotated[
        float,
        Field(
            description="Movement velocity as percent (1–100)",
            example=50.0,
            ge=VelocityLimits.MIN_PERCENT,
            le=VelocityLimits.MAX_PERCENT
        ),
    ] = VelocityLimits.DEFAULT_PERCENT

    acceleration_percent: Annotated[
        float,
        Field(
            description="Movement acceleration as percent (1–100)",
            example=50.0,
            ge=VelocityLimits.MIN_PERCENT,
            le=VelocityLimits.MAX_PERCENT
        ),
    ] = VelocityLimits.DEFAULT_PERCENT

class IgusCommandResponse(BaseResponse):
    """Response for any synchronous motor command."""

class IgusAsyncResponse(BaseAsyncResponse):
    """Response for an async motor command (task)."""

class IgusPositionResponse(BaseApiModel):
    """Only current position of the motor."""
    position_cm: float = Field(
        PositionLimits.DEFAULT_CM,
        description="Current position in centimeters (0.00–120.00)",
        example=50.0
    )

    @validator("position_cm")
    @classmethod
    def validate_position_cm(cls, v):
        if not (PositionLimits.MIN_CM <= v <= PositionLimits.MAX_CM):
            raise ValueError(f"Position must be between {PositionLimits.MIN_CM} and {PositionLimits.MAX_CM} cm")
        return v

class IgusMotionResponse(BaseApiModel):
    """Current motion state of the motor."""
    is_moving: bool = Field(
        ..., description="True if the motor is currently moving", example=False
    )

# ============================================================================
# AGV GEOMETRY AND VELOCITY
# ============================================================================

class SymovoPose(BaseApiModel):
    """Pose of the AGV on the map."""
    x_m: float = Field(..., description="X coordinate in meters", example=0.0)
    y_m: float = Field(..., description="Y coordinate in meters", example=0.0)
    theta_deg: float = Field(..., description="Orientation angle in degrees", example=0.0)
    map_id: int = Field(..., description="map code", example=0)

class SymovoVelocity(BaseApiModel):
    """Current AGV velocity components."""
    vx_m_s: float = Field(..., description="Linear velocity X (m/s)", example=0.0)
    vy_m_s: float = Field(..., description="Linear velocity Y (m/s)", example=0.0)
    omega_rad_s: float = Field(..., description="Angular velocity (rad/s)", example=0.0)

# ============================================================================
# AGV STATE AND STATUS
# ============================================================================

class SymovoStatusResponse(BaseApiModel):
    """Current status of the Symovo AGV."""
    online: bool = Field(..., description="True if AGV is online", example=True)
    last_update_time: OptionalStr = Field(None, description="Timestamp of last status update (ISO 8601)", example="2024-01-01T12:00:00Z")
    id: OptionalStr = Field(None, description="AGV identifier", example="agv01")
    name: OptionalStr = Field(None, description="AGV display name", example="AGV 1")
    pose: SymovoPose = Field(..., description="Current AGV pose on map")
    velocity: SymovoVelocity = Field(..., description="Current AGV velocity")
    state: OptionalStr = Field(None, description="Current AGV state", example="IDLE")
    battery_level_percent: OptionalFloat = Field(None, description="Battery level (%)", example=90.0)
    state_flags: Optional[JsonDict] = Field(None, description="Internal state flags (object/bitfield)")
    robot_ip: OptionalStr = Field(None, description="AGV IP address", example="192.168.1.100")
    replication_port: OptionalInt = Field(None, description="Replication TCP port", example=8004)
    api_port: OptionalInt = Field(None, description="REST API TCP port", example=8004)
    iot_port: OptionalInt = Field(None, description="IoT/MQTT port", example=9000)
    last_seen: OptionalStr = Field(None, description="Last time AGV responded (ISO 8601)", example="2024-01-01T12:00:01Z")
    enabled: OptionalBool = Field(None, description="True if AGV is enabled for operation", example=True)
    last_update_epoch: OptionalFloat = Field(None, description="Last update time (epoch)", example=1710000000.0)
    attributes: Optional[JsonDict] = Field(None, description="Additional attributes (object, optional)")
    planned_path_edges: Optional[JsonList] = Field(None, description="Currently planned path edges (list of edge objects)")

# ============================================================================
# STATUS AND RESULT OF UNIVERSAL COMMANDS
# ============================================================================

class NewJobResponse(BaseApiModel):
    """Response after starting a new AGV job."""
    status: str = Field(..., description="Job operation status", example="ok")
    message: str = Field(..., description="Human-readable status message", example="Job started")
    result: JsonDict = Field(..., description="Job result object (varies by job type)")

class GenericResult(BaseApiModel):
    """General result wrapper."""
    status: str = Field(..., description="Operation status", example="ok")
    result: JsonDict = Field(..., description="Result object or value")

# ============================================================================
# AGV MOVEMENT COMMANDS
# ============================================================================

class MoveToPoseRequest(BaseApiModel):
    """Request parameters for commanding AGV to a pose."""
    x_m: float = Field(..., description="Target X position in meters", example=1.0)
    y_m: float = Field(..., description="Target Y position in meters", example=2.0)
    theta_deg: float = Field(0.0, description="Target orientation", example=0.0)
    map_id: int = Field(..., description="map code", example=0)
    max_speed_m_s: OptionalFloat = Field(None, description="Maximum speed (m/s)", example=0.5)

# ============================================================================
# xARM MANIPULATOR TYPES
# ============================================================================

class XarmJointsDict(BaseApiModel):
    j1: float = Field(..., description="Joint 1 angle in degrees")
    j2: float = Field(..., description="Joint 2 angle in degrees")
    j3: float = Field(..., description="Joint 3 angle in degrees")
    j4: float = Field(..., description="Joint 4 angle in degrees")
    j5: float = Field(..., description="Joint 5 angle in degrees")
    j6: float = Field(..., description="Joint 6 angle in degrees")

class ToolOffsetLimits:
    MIN_MM = -1000.0
    MAX_MM = 1000.0

class VelocityLimits:
    MIN_PERCENT = 1.0
    MAX_PERCENT = 100.0

class XarmMoveWithToolParams(BaseApiModel):
    """Move manipulator by tool offsets."""

    x_offset_mm: Annotated[
        float,
        Field(
            description="Tool X offset (mm)",
            example=0.0,
            ge=ToolOffsetLimits.MIN_MM,
            le=ToolOffsetLimits.MAX_MM,
        ),
    ]
    y_offset_mm: Annotated[
        float,
        Field(
            description="Tool Y offset (mm)",
            example=0.0,
            ge=ToolOffsetLimits.MIN_MM,
            le=ToolOffsetLimits.MAX_MM,
        ),
    ]
    z_offset_mm: Annotated[
        float,
        Field(
            description="Tool Z offset (mm)",
            example=0.0,
            ge=ToolOffsetLimits.MIN_MM,
            le=ToolOffsetLimits.MAX_MM,
        ),
    ]
    velocity_percent: Annotated[
        float,
        Field(
            description="Manipulator speed (%)",
            example=50.0,
            ge=VelocityLimits.MIN_PERCENT,
            le=VelocityLimits.MAX_PERCENT,
        ),
    ]
    reset_faults: Annotated[
        StrictBool,
        Field(
            description="Reset errors and reinitialize before move. Must be boolean true/false.",
            example=False,
        ),
    ] = False   # default only here
    roll_offset_deg: Optional[float] = Field(None, description="Roll offset in degrees")
    pitch_offset_deg: Optional[float] = Field(None, description="Pitch offset in degrees")
    yaw_offset_deg: Optional[float] = Field(None, description="Yaw offset in degrees")

from pydantic import BaseModel, Field, StrictFloat, StrictBool, validator

class XarmMoveWithPoseParams(BaseApiModel):
    """Move manipulator to a named pose."""
    name: Annotated[str, Field(description="Target pose name", example="READY_SECTION_CENTER", min_length=1)]
    velocity_percent: Annotated[float, Field(description="Manipulator speed (%)", example=50.0, ge=1.0, le=100.0)]
    reset_faults: Annotated[bool, Field(description="Reset errors and reinitialize before move. Must be boolean true/false.", example=False)] = False

class XarmMoveWithJointsParams(BaseApiModel):
    """Move manipulator to specific joint angles."""
    j1: float = Field(..., description="Joint 1 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    j2: float = Field(..., description="Joint 2 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    j3: float = Field(..., description="Joint 3 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    j4: float = Field(..., description="Joint 4 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    j5: float = Field(..., description="Joint 5 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    j6: float = Field(..., description="Joint 6 angle (degrees)", example=0.0, ge=JointLimits.MIN_DEG, le=JointLimits.MAX_DEG)
    velocity_percent: float = Field(..., description="Manipulator speed (%)", example=50.0, ge=1.0, le=100.0)
    reset_faults: bool = Field(False, description="Reset errors and reinitialize before move. Must be boolean true/false.", example=False)

class XarmPositionResponse(BaseApiModel):
    """Current position response with pose name and joint positions."""
    name: str = Field(..., description="Current pose name", example="READY_SECTION_CENTER")
    points: XarmJointsDict = Field(
        ...,
        description="Current joint positions in degrees",
        example={"j1": 0, "j2": 10, "j3": 20, "j4": 30, "j5": 40, "j6": 50}
    )

class XarmMoveWithJointsDictParams(BaseApiModel):
    """Move manipulator along a sequence of joint positions."""
    points: List[XarmJointsDict] = Field(
        ..., description="List of joint positions, each with keys 'j1'..'j6'",
        example=[
            {"j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0},
            {"j1": -10, "j2": -10, "j3": -10, "j4": -10, "j5": -10, "j6": -10},
        ]
    )
    velocity_percent: float = Field(..., description="Manipulator speed (%)", example=50.0, ge=1.0, le=100.0)
    reset_faults: bool = Field(False, description="Reset errors and reinitialize before move. Must be boolean true/false.", example=False)

class XarmCommandResponse(BaseResponse):
    """Response for synchronous manipulator commands."""

class XarmAsyncResponse(BaseAsyncResponse):
    """Response for async manipulator commands."""

class XarmStatusResponse(BaseApiModel):
    """Full status of the manipulator controller."""
    alive: bool = Field(..., description="Controller heartbeat flag", example=True)
    connected: bool = Field(..., description="True if xArm is connected", example=False)
    state_code: int = Field(..., description="Internal controller state code", example=0)
    has_err_warn: bool = Field(..., description="True if warnings or errors present", example=True)
    has_error: bool = Field(..., description="True if an error is active", example=False)
    has_warn: bool = Field(..., description="True if warnings are present", example=True)
    error_code: int = Field(..., description="Current error code (0 means no error)", example=0)

class XarmJointsPositionResponse(BaseApiModel):
    name: str = Field(..., description="Name of the position, e.g. CURRENT")
    joints: XarmJointsDict = Field(..., description="Current joint angles (degrees)")

class RobotSystemStatus(BaseApiModel):
    """Overall robot system status."""
    ready: bool = Field(..., description="True if all subsystems are ready for operation")
    message: str = Field(..., description="If not ready, explanation; empty if ready")
    igus: Union["IgusStatusResponse", "ErrorStatus"]
    symovo: Union["SymovoStatusResponse", "ErrorStatus", dict]
    xarm: Optional[Any] = Field(None, description="xArm status payload: full websocket report (when available) with optional `summary` and `updated_ts`.")

# ============================================================================
# SYMOVO TELEOP (JOYSTICK → AGV DRIVE)
# ============================================================================

# Allowed ranges per symovo_teleop.txt: duration 0.1–0.5 typical, linear up to ~1 m/s, angular up to ~0.8 rad/s
SYMOVO_TELEOP_DURATION_MIN = 0.05
SYMOVO_TELEOP_DURATION_MAX = 2.0
SYMOVO_TELEOP_LINEAR_MIN = 0.01
SYMOVO_TELEOP_LINEAR_MAX = 1.0
SYMOVO_TELEOP_ANGULAR_MIN = 0.01
SYMOVO_TELEOP_ANGULAR_MAX = 2.0


class SymovoTeleopConfigResponse(BaseApiModel):
    """
    Current AGV teleop parameters for joystick (buttons 4=W, 6=S, 7=A, 5=D).
    Used when sending commands to SYMOVO_TELEOP_MOVE_URL (default :7906/move/speed).
    """
    duration: float = Field(
        ...,
        title="Segment duration",
        ge=SYMOVO_TELEOP_DURATION_MIN,
        le=SYMOVO_TELEOP_DURATION_MAX,
        description="Duration of one motion segment, s. Recommended 0.1–0.5.",
        example=0.25,
    )
    linear_m_s: float = Field(
        ...,
        title="Linear speed",
        ge=SYMOVO_TELEOP_LINEAR_MIN,
        le=SYMOVO_TELEOP_LINEAR_MAX,
        description="Linear speed when pressing W/S, m/s. Typical up to 0.5–1.0.",
        example=0.1,
    )
    angular_rad_s: float = Field(
        ...,
        title="Angular speed",
        ge=SYMOVO_TELEOP_ANGULAR_MIN,
        le=SYMOVO_TELEOP_ANGULAR_MAX,
        description="Angular speed when pressing A/D, rad/s. Positive = left (CCW).",
        example=0.5,
    )


class SymovoTeleopConfigUpdate(BaseApiModel):
    """
    AGV teleop parameter update (partial: only provided fields are applied, the rest unchanged).
    """
    duration: Optional[float] = Field(
        None,
        title="Segment duration",
        ge=SYMOVO_TELEOP_DURATION_MIN,
        le=SYMOVO_TELEOP_DURATION_MAX,
        description="Segment duration, s. Allowed range 0.05–2.0.",
        example=0.25,
    )
    linear_m_s: Optional[float] = Field(
        None,
        title="Linear speed",
        ge=SYMOVO_TELEOP_LINEAR_MIN,
        le=SYMOVO_TELEOP_LINEAR_MAX,
        description="Linear speed W/S, m/s. Allowed range 0.01–1.0.",
        example=0.1,
    )
    angular_rad_s: Optional[float] = Field(
        None,
        title="Angular speed",
        ge=SYMOVO_TELEOP_ANGULAR_MIN,
        le=SYMOVO_TELEOP_ANGULAR_MAX,
        description="Angular speed A/D, rad/s. Allowed range 0.01–2.0.",
        example=0.5,
    )

    class Config:
        extra = "forbid"


class SymovoDriveModeRequest(BaseApiModel):
    """
    Enable or disable AGV drive mode (motors).
    Drive mode must be on (enable=true) for the robot to respond to joystick teleop.
    Per spec, calls PUT .../drive_mode?enable=true|false on the main API (port 7905).
    """
    enable: bool = Field(
        ...,
        title="Enable drive mode",
        description="true — enable motors and prepare for teleop (deactivates charging station if needed); false — disable motors.",
        example=True,
    )


class SymovoDriveModeResponse(BaseApiModel):
    """
    Result of toggling drive mode. Backend (nav2adapter) response is forwarded in result.
    """
    success: bool = Field(..., title="Success", description="True if the backend request returned 2xx.")
    enable: bool = Field(..., title="Requested value", description="Whether drive mode was requested on.")
    result: Optional[Any] = Field(None, title="Backend response", description="Body of PUT /drive_mode response if any.")
    detail: Optional[str] = Field(None, title="Error detail", description="Error text when success=false.")

    class Config:
        extra = "forbid"

# ============================================================================
# WEBSOCKET AND JOYSTICK TYPES
# ============================================================================

class JoystickData(BaseApiModel):
    axes: FloatDict = Field(
        ..., 
        description="Joystick axes values (-1.0 to 1.0)", 
        example={"axis0": 0.5, "axis1": -0.3}
    )
    buttons: BoolDict = Field(
        ..., 
        description="Joystick button states", 
        example={"button0": True, "button1": False}
    )
    class Config:
            extra = "ignore"

class JoystickState(BaseApiModel):
    """Current state of a virtual joystick."""
    axes: FloatDict = Field(..., description="Current axes values", example={"x": 0.0, "y": 0.0})
    buttons: BoolDict = Field(..., description="Current button states", example={"button1": False})
    activities: BoolDict = Field(..., description="Activity for device groups", example={"manipulator": True, "lift": False})

class WebSocketResponse(BaseApiModel):
    """Standard WebSocket response."""
    status: str = Field(..., description="Response status", example="ok")

# ============================================================================
# LED AND ARDUINO TYPES
# ============================================================================

class LedCommand(BaseApiModel):
    """LED command for Arduino."""
    command: str = Field(..., description="LED command string", example="LED_ON")

class ArduinoResponse(BaseResponse):
    """Response from Arduino LED controller."""

# ============================================================================
# TRAJECTORY TYPES
# ============================================================================

class TrajectoryConfig(BaseApiModel):
    """Trajectory configuration data."""
    points: JsonList = Field(..., description="Trajectory waypoints")
    velocity: float = Field(..., description="Trajectory velocity", example=50.0, gt=0.0)
    acceleration: float = Field(..., description="Trajectory acceleration", example=25.0, gt=0.0)

class TrajectorySaveResponse(BaseApiModel):
    """Response for trajectory save operation."""
    status: str = Field(..., description="Operation status", example="ok")
    message: str = Field(..., description="Status message", example="Trajectory configuration saved.")

class RobotAsyncResponse(BaseAsyncResponse):
    """Response for async robot commands."""

# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Coordinates and offsets
    'ProductLocation', 'XarmToolOffsets',
    
    # Requests
    'DefaultMoveRequest', 'RobotMoveRequest',
    
    # Movement responses
    'SymovoMoveResult', 'IgusMoveResult', 'XarmMoveResult',
    
    # Coordinated responses
    'RobotMoveResult', 'RobotMoveBoxResult', 'RobotTransportPositionResult',
    
    # Subsystem statuses
    'ErrorStatus', 'IgusStatusResponse', 'TaskStatusResponse', 'IgusMoveParams',
    'IgusCommandResponse', 'IgusAsyncResponse', 'IgusPositionResponse', 'IgusMotionResponse',
    
    # AGV types
    'SymovoPose', 'SymovoVelocity', 'SymovoStatusResponse', 'NewJobResponse', 
    'GenericResult', 'MoveToPoseRequest',
    
    # xArm types
    'XarmJointsDict', 'XarmMoveWithToolParams', 'XarmMoveWithPoseParams',
    'XarmMoveWithJointsParams', 'XarmPositionResponse', 'XarmMoveWithJointsDictParams',
    'XarmCommandResponse', 'XarmAsyncResponse', 'XarmStatusResponse', 'XarmJointsPositionResponse',
    
    # System types
    'RobotSystemStatus',
    
    # Symovo teleop (joystick → AGV)
    'SymovoTeleopConfigResponse', 'SymovoTeleopConfigUpdate',
    'SymovoDriveModeRequest', 'SymovoDriveModeResponse',
    'SYMOVO_TELEOP_DURATION_MIN', 'SYMOVO_TELEOP_DURATION_MAX',
    'SYMOVO_TELEOP_LINEAR_MIN', 'SYMOVO_TELEOP_LINEAR_MAX',
    'SYMOVO_TELEOP_ANGULAR_MIN', 'SYMOVO_TELEOP_ANGULAR_MAX',

    # WebSocket and joystick
    'JoystickData', 'JoystickState', 'WebSocketResponse',
    
    # LED and Arduino
    'LedCommand', 'ArduinoResponse',
    
    # Trajectories
    'TrajectoryConfig', 'TrajectorySaveResponse', 'RobotAsyncResponse',
]
