"""Pydantic models for API v1 endpoints."""

from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from app.version import SERVER_VERSION

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
    details: FaultDetails | None = Field(None, description="Detailed diagnostics (populated when fault is active)")


class DriveStatus(BaseModel):
    """Drive status model."""
    online: DriveOnlineState = Field(..., description="Drive online state")
    last_poll_ts: int | None = Field(None, description="Last poll wall-clock timestamp (unix ms)")
    poll_period_ms: float | None = Field(None, description="Poll period in milliseconds")

    cia402_state: CiA402State = Field(..., description="Current CiA 402 state")
    mode_display: OperationMode | None = Field(None, description="Current operation mode")

    statusword: int = Field(..., description="Raw statusword value (16-bit)")
    status_bits: dict[str, bool] = Field(default_factory=dict, description="Parsed statusword bits")
    remote: bool | None = Field(None, description="Remote control enabled (bit 9)")
    enabled: bool | None = Field(None, description="Operation enabled")

    position: int | None = Field(None, description="Current position in drive units")
    velocity: int | None = Field(None, description="Current velocity in drive units/s")

    is_moving: bool = Field(True, description="Whether the drive is currently in motion (fail-safe default: True)")
    is_homed: bool = Field(False, description="Whether homing has been completed")

    fault: FaultInfo = Field(..., description="Fault information")


class ProfileConfig(BaseModel):
    """Motion profile configuration."""
    velocity: int = Field(..., gt=0, description="Profile velocity in drive units/s")
    acceleration: int = Field(..., gt=0, description="Profile acceleration in drive units/s²")
    deceleration: int = Field(..., gt=0, description="Profile deceleration in drive units/s²")


class MoveToPositionRequest(BaseModel):
    """Move to position request."""
    target_position: int = Field(..., description="Target position in drive units")
    relative: bool = Field(False, description="Whether position is relative")
    profile: ProfileConfig = Field(..., description="Motion profile")
    timeout_ms: int = Field(20000, gt=0, description="Timeout in milliseconds")


class JogMoveRequest(BaseModel):
    """Jog move request."""
    direction: Literal["positive", "negative"] = Field(..., description="Direction: 'positive' or 'negative'")
    speed: float | None = Field(None, gt=0, description="Speed in drive units/s (optional; local default is used when omitted)")
    ttl_ms: int = Field(200, ge=50, le=5000, description="Watchdog TTL in milliseconds")


class ReferenceRequest(BaseModel):
    """Reference/homing request."""
    timeout_ms: int = Field(60000, gt=0, description="Timeout in milliseconds")


class FaultResetRequest(BaseModel):
    """Fault reset request."""
    auto_enable: bool = Field(True, description="Automatically re-enable operation after fault reset")


class StopRequest(BaseModel):
    """Stop request."""
    mode: Literal["quick_stop", "halt"] = Field("quick_stop", description="Stop mode: 'quick_stop' or 'halt'")


# ---------------------------------------------------------------------------
# Response result models (data payloads inside ApiEnvelope)
# ---------------------------------------------------------------------------

class TelemetryResponse(BaseModel):
    """Drive telemetry snapshot."""
    ts: int = Field(..., description="Unix timestamp in milliseconds")
    position: int | None = Field(None, description="Current position in drive units")
    velocity: int | None = Field(None, description="Current velocity in drive units/s")
    statusword: int = Field(..., description="Raw statusword value (16-bit)")
    cia402_state: str = Field(..., description="Current CiA 402 state name")


class CommandTrace(BaseModel):
    """Command trace snapshot for diagnostics correlation."""
    ts: int = Field(..., description="Unix timestamp in milliseconds")
    operation: str = Field(..., description="Operation name")
    request_id: str | None = Field(None, description="HTTP request ID")
    command_id: str | None = Field(None, description="Command UUID")
    op_id: str | None = Field(None, description="Short operation ID passed to driver")


class TraceResponse(BaseModel):
    """Latest command trace response."""
    has_trace: bool = Field(..., description="Whether a trace is available")
    trace: CommandTrace | None = Field(None, description="Trace data if available")


class MoveToPositionResult(BaseModel):
    """Result of a move_to_position command."""
    target_position: int = Field(..., description="Actual target position sent to drive")
    aborted: bool = Field(False, description="True if motion was aborted by a stop command")


class JogResult(BaseModel):
    """Result of a jog_start or jog_update command."""
    velocity: int = Field(..., description="Commanded velocity (negative = negative direction)")
    direction: str = Field(..., description="Direction: 'positive' or 'negative'")


class JogStopResult(BaseModel):
    """Result of a jog_stop command."""
    stopped: bool = Field(..., description="True if jog was stopped")


class StopResult(BaseModel):
    """Result of a stop command."""
    mode: str = Field(..., description="Stop mode that was applied")


class ReferenceResult(BaseModel):
    """Result of a reference/homing command."""
    homed: bool = Field(..., description="True if homing completed successfully")
    result: str | None = Field(None, description="Driver result message")
    aborted: bool = Field(False, description="True if homing was aborted by a stop command")


class FaultResetResult(BaseModel):
    """Result of a fault_reset command."""
    auto_enable_requested: bool = Field(..., description="Whether auto-enable was requested (not whether recovery succeeded)")
    fault_cleared: bool | None = Field(
        ...,
        description=(
            "Whether the fault bit was cleared after reset. "
            "True = fault cleared. False = fault still active after reset. "
            "None = reset command was sent but the post-reset status read failed "
            "(verification could not be performed — does NOT mean the reset itself failed)."
        ),
    )
    new_state: str | None = Field(None, description="CiA 402 state after reset (only populated when fault_cleared=True)")
    previous_fault: dict[str, Any] | None = Field(None, description="Fault diagnostics captured before reset")

