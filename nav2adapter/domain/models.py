"""
Domain models for navigation commands and status.
"""
from typing import Optional
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class NavigationStatusEnum(str, Enum):
    """AE.HUB navigation status values."""
    IDLE = "idle"
    NAVIGATING = "navigating"
    ARRIVED = "arrived"
    ERROR = "error"


class NavigationCommand(BaseModel):
    """Navigation command from MQTT."""
    command_id: str = Field(..., description="UUIDv4 command identifier")
    timestamp: str = Field(..., description="ISO8601 timestamp")
    target_id: str = Field(..., description="Target position ID (e.g., position_A)")
    priority: Optional[int] = Field(default=None, description="Optional priority")


class NavigationStatus(BaseModel):
    """Navigation status published to MQTT."""
    status: NavigationStatusEnum = Field(..., description="Current navigation status")
    goal_id: Optional[str] = Field(default=None, description="Active command_id or null")
    progress_percent: int = Field(default=0, ge=0, le=100, description="Progress 0-100")
    eta_seconds: Optional[float] = Field(default=None, description="Estimated time to arrival")
    error_reason: Optional[str] = Field(default=None, description="Error reason if status=error")


class PositionStatus(BaseModel):
    """Position status published to MQTT."""
    x: float = Field(..., description="X coordinate in meters")
    y: float = Field(..., description="Y coordinate in meters")
    theta: float = Field(..., description="Heading in radians")
    frame_id: str = Field(default="map", description="Frame ID (usually 'map')")


class RobotReadiness(BaseModel):
    """Robot readiness status."""
    ready: bool = Field(..., description="Whether robot is ready for navigation")
    error_detail: Optional[str] = Field(default=None, description="Error detail if not ready")


class ActiveTransport(BaseModel):
    """Active transport tracking."""
    command_id: str = Field(..., description="Command ID that created this transport")
    transport_id: str = Field(..., description="Symovo transport ID")
    state: int = Field(..., description="Symovo transport state (enum)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp")
    target_id: Optional[str] = Field(default=None, description="Target position ID")
    generation: int = Field(default=0, description="Generation token to prevent stale watcher publications")


class Pose2D(BaseModel):
    """Minimal 2D pose for progress computation (map frame)."""
    x: float = Field(..., description="X coordinate in meters")
    y: float = Field(..., description="Y coordinate in meters")
    map_id: Optional[int] = Field(default=None, description="Optional map id")


class NavigationSession(BaseModel):
    """
    Tracks start/goal/current progress for a command_id, independent of Symovo transport states.
    Progress is computed from straight-line distance and clamped to be monotonic.
    """
    command_id: str = Field(..., description="Command ID")
    target_id: Optional[str] = Field(default=None, description="Target name (same as command.target_id)")
    start: Pose2D = Field(..., description="Start pose snapshot at acceptance time")
    goal: Pose2D = Field(..., description="Goal pose (resolved from DB)")
    total_dist_m: float = Field(..., ge=0.0, description="Baseline straight-line distance from start to goal")
    min_remaining_dist_m: float = Field(..., ge=0.0, description="Minimum remaining distance seen so far (monotonic)")
    progress_percent: int = Field(default=0, ge=0, le=100, description="Monotonic progress 0-100")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
