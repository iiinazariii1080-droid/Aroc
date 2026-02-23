"""Pydantic models for API v1 endpoints."""

from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from app.version import SERVER_VERSION
from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State

T = TypeVar('T')


class DriveOnlineState(str, Enum):
    """Drive online state."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class CiA402State(str, Enum):
    """CiA 402 state machine states."""
    NOT_READY_TO_SWITCH_ON = "not_ready_to_switch_on"
    SWITCH_ON_DISABLED = "switch_on_disabled"
    READY_TO_SWITCH_ON = "ready_to_switch_on"
    SWITCHED_ON = "switched_on"
    OPERATION_ENABLED = "operation_enabled"
    QUICK_STOP_ACTIVE = "quick_stop_active"
    FAULT_REACTION_ACTIVE = "fault_reaction_active"
    FAULT = "fault"
    UNKNOWN = "unknown"


class OperationMode(str, Enum):
    """Operation mode values."""
    PP = "PP"  # Profile Position (1)
    PV = "PV"  # Profile Velocity (3)
    HOMING = "HOMING"  # Homing (6)
    UNKNOWN = "UNKNOWN"


_MODE_MAP: dict[int, OperationMode] = {
    1: OperationMode.PP,
    3: OperationMode.PV,
    6: OperationMode.HOMING,
}


def mode_display_to_operation_mode(mode_display: int | None) -> OperationMode:
    """Convert mode_display integer to OperationMode enum."""
    if mode_display is None:
        return OperationMode.UNKNOWN
    return _MODE_MAP.get(int(mode_display) & 0xFF, OperationMode.UNKNOWN)


def driver_cia402_state_to_api_state(state: DriverCiA402State) -> CiA402State:
    """Convert driver CiA402State to API CiA402State."""
    return _CIA402_STATE_MAP.get(state, CiA402State.UNKNOWN)


_CIA402_STATE_MAP: dict[DriverCiA402State, CiA402State] = {
    DriverCiA402State.NOT_READY_TO_SWITCH_ON: CiA402State.NOT_READY_TO_SWITCH_ON,
    DriverCiA402State.SWITCH_ON_DISABLED: CiA402State.SWITCH_ON_DISABLED,
    DriverCiA402State.READY_TO_SWITCH_ON: CiA402State.READY_TO_SWITCH_ON,
    DriverCiA402State.SWITCHED_ON: CiA402State.SWITCHED_ON,
    DriverCiA402State.OPERATION_ENABLED: CiA402State.OPERATION_ENABLED,
    DriverCiA402State.QUICK_STOP_ACTIVE: CiA402State.QUICK_STOP_ACTIVE,
    DriverCiA402State.FAULT_REACTION_ACTIVE: CiA402State.FAULT_REACTION_ACTIVE,
    DriverCiA402State.FAULT: CiA402State.FAULT,
    DriverCiA402State.UNKNOWN: CiA402State.UNKNOWN,
}


class ApiError(BaseModel):
    """API error model."""
    code: str = Field(..., description="Error code (e.g., DRIVE_OFFLINE, PRECONDITION_FAILED)")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(None, description="Additional error details")


class Meta(BaseModel):
    """Response metadata."""
    ts: int = Field(..., description="Unix timestamp in milliseconds")
    request_id: str | None = Field(None, description="Request ID for tracing")
    command_id: str | None = Field(None, description="Command ID if applicable")
    server_version: str = Field(default=SERVER_VERSION, description="Server version")


class ApiEnvelope(BaseModel, Generic[T]):
    """Universal API response envelope."""
    ok: bool = Field(..., description="Whether the request succeeded")
    data: T | None = Field(None, description="Response data if ok=True")
    error: ApiError | None = Field(None, description="Error information if ok=False")
    meta: Meta = Field(..., description="Response metadata")


class FaultDetails(BaseModel):
    """Detailed fault diagnostics from the drive (OD 0x603F, 0x1001, 0x1003)."""
    error_code: str | None = Field(None, description="Error code (hex) from OD 0x603F")
    error_register: str | None = Field(None, description="Error register (hex) from OD 0x1001")
    history: list[str] | None = Field(None, description="Error history from OD 0x1003")


class FaultInfo(BaseModel):
    """Fault information."""
    active: bool = Field(..., description="Whether a fault is currently active")
    code: int | None = Field(None, description="Fault code if available")
    description: str | None = Field(None, description="Fault description if available")
    details: FaultDetails | None = Field(None, description="Detailed diagnostics (populated when fault is active)")


class DriveStatus(BaseModel):
    """Drive status model."""
    online: DriveOnlineState = Field(..., description="Drive online state")
    last_poll_ts: int | None = Field(None, description="Last poll timestamp (unix ms)")
    poll_period_ms: float | None = Field(None, description="Poll period in milliseconds")
    poll_latency_ms: float | None = Field(None, description="Last poll latency in milliseconds")
    
    cia402_state: CiA402State = Field(..., description="Current CiA 402 state")
    mode_display: OperationMode | None = Field(None, description="Current operation mode")
    
    statusword: int = Field(..., description="Raw statusword value (16-bit)")
    controlword: int | None = Field(None, description="Current controlword value")
    status_bits: dict[str, bool] = Field(default_factory=dict, description="Parsed statusword bits")
    remote: bool | None = Field(None, description="Remote control enabled (bit 9)")
    enabled: bool | None = Field(None, description="Operation enabled")
    
    position: float | None = Field(None, description="Current position in drive units")
    velocity: float | None = Field(None, description="Current velocity in drive units/s")
    torque: float | None = Field(None, description="Current torque (if available)")
    
    fault: FaultInfo = Field(..., description="Fault information")
    last_error: ApiError | None = Field(None, description="Last error if any")


class ProfileConfig(BaseModel):
    """Motion profile configuration."""
    velocity: float = Field(..., gt=0, description="Profile velocity in drive units/s")
    acceleration: float = Field(..., gt=0, description="Profile acceleration in drive units/s²")
    deceleration: float = Field(..., gt=0, description="Profile deceleration in drive units/s²")
    jerk: float | None = Field(None, gt=0, description="Jerk (optional)")


class PositionLimits(BaseModel):
    """Position limits."""
    max_position: float | None = Field(None, description="Maximum position")
    min_position: float | None = Field(None, description="Minimum position")


class MoveBehavior(BaseModel):
    """Move behavior configuration."""
    halt_on_new_command: bool = Field(True, description="Halt on new command")
    require_operation_enabled: bool = Field(True, description="Require operation enabled")


class MoveToPositionRequest(BaseModel):
    """Move to position request."""
    target_position: float = Field(..., description="Target position in drive units")
    relative: bool = Field(False, description="Whether position is relative")
    profile: ProfileConfig = Field(..., description="Motion profile")
    limits: PositionLimits | None = Field(None, description="Position limits")
    behavior: MoveBehavior | None = Field(None, description="Move behavior")
    timeout_ms: int = Field(20000, gt=0, description="Timeout in milliseconds")


class JogMoveRequest(BaseModel):
    """Jog move request."""
    direction: Literal["positive", "negative"] = Field(..., description="Direction: 'positive' or 'negative'")
    speed: float | None = Field(None, gt=0, description="Speed in drive units/s (optional; local default is used when omitted)")
    duration_ms: int | None = Field(None, gt=0, description="Duration in milliseconds (None = until stop)")
    ttl_ms: int = Field(200, ge=50, le=5000, description="Watchdog TTL in milliseconds")


class ReferenceRequest(BaseModel):
    """Reference/homing request."""
    method: int | None = Field(None, description="Homing method")
    speed_fast: float | None = Field(None, gt=0, description="Fast speed")
    speed_slow: float | None = Field(None, gt=0, description="Slow speed")
    acceleration: float | None = Field(None, gt=0, description="Acceleration")
    timeout_ms: int = Field(60000, gt=0, description="Timeout in milliseconds")


class FaultResetRequest(BaseModel):
    """Fault reset request."""
    after_reset: dict[str, bool] | None = Field(None, description="Actions after reset")
    timeout_ms: int = Field(15000, gt=0, description="Timeout in milliseconds")


class StopRequest(BaseModel):
    """Stop request."""
    mode: Literal["quick_stop", "halt"] = Field("quick_stop", description="Stop mode: 'quick_stop' or 'halt'")
    timeout_ms: int = Field(5000, gt=0, description="Timeout in milliseconds")

