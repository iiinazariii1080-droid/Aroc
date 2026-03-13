from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class ErrorStatus(BaseModel):
    """Canonical error payload used inside HTTPException.detail.

    Example: {"error": "Device is busy"} or {"error": {"type": "...", "msg": "..."}}
    """
    error: Dict[str, Any]


class SymovoPose(BaseModel):
    x_m: Optional[float] = Field(default=None, description="X coordinate in meters")
    y_m: Optional[float] = Field(default=None, description="Y coordinate in meters")
    theta_deg: Optional[float] = Field(default=None, description="Heading in degrees")
    map_id: Optional[str | int] = Field(default=None, description="Current map id")


class SymovoVelocity(BaseModel):
    vx_m_s: Optional[float] = Field(default=None, description="Linear velocity X, m/s")
    vy_m_s: Optional[float] = Field(default=None, description="Linear velocity Y, m/s")
    omega_deg_s: Optional[float] = Field(default=None, description="Angular velocity, deg/s")


class SymovoStatusResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "online": True,
            "last_update_time": "2025-01-01T12:00:00Z",
            "id": "15",
            "name": "AGV-15",
            "pose": {"x_m": 1.23, "y_m": 4.56, "theta_deg": 45.0, "map_id": 2},
            "velocity": {"vx_m_s": 0.1, "vy_m_s": 0.0, "omega_deg_s": 0.0},
            "state": "IDLE",
            "battery_level_percent": 84.5,
            "state_flags": {"emergency_stop": False},
            "robot_ip": "192.168.1.100",
            "replication_port": 7001,
            "api_port": 443,
            "iot_port": 1883,
            "last_seen": "2025-01-01T12:00:00Z",
            "enabled": True,
            "last_update_epoch": 1735723200,
            "attributes": {"payload": "empty"},
            "planned_path_edges": []
        }
    })
    online: bool
    last_update_time: Optional[str] = Field(default=None)
    id: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None)
    pose: SymovoPose
    velocity: SymovoVelocity
    state: Optional[str] = Field(default=None)
    battery_level_percent: Optional[float] = Field(default=None)
    state_flags: Optional[Dict[str, Any]] = Field(default=None)
    robot_ip: Optional[str] = Field(default=None)
    replication_port: Optional[int] = Field(default=None)
    api_port: Optional[int] = Field(default=None)
    iot_port: Optional[int] = Field(default=None)
    last_seen: Optional[str] = Field(default=None)
    enabled: Optional[bool] = Field(default=None)
    last_update_epoch: Optional[int | float] = Field(default=None)
    attributes: Optional[Dict[str, Any]] = Field(default=None)
    planned_path_edges: Optional[Any] = Field(default=None)


class GenericResponse(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_extra={
        "example": {"status": "ok"}
    })


# ============================================================================
# POSITION PARAMS (saved positions)
# ============================================================================
class PositionLocation(BaseModel):
    """Location coordinates required for navigation."""
    x_m: float = Field(..., description="X coordinate in meters")
    y_m: float = Field(..., description="Y coordinate in meters")
    theta_deg: float = Field(0.0, description="Heading in degrees")
    map_id: int = Field(0, description="Map identifier")


class PositionParams(BaseModel):
    """Validated params for a saved robot position.

    Accepts both nested ``{"location": {"x_m": …}}`` and flat
    ``{"x_m": …}`` formats.  Flat keys are normalised into
    ``location`` before validation so storage is always consistent.
    Only navigation-related fields are accepted (coordinates + heading).
    Lift and manipulator data is managed by robot_service.
    """
    model_config = ConfigDict(extra="forbid")

    location: PositionLocation
    description: Optional[str] = Field(default=None, description="Human-readable description")
    max_speed_m_s: Optional[float] = Field(default=None, description="Max navigation speed, m/s")

    @model_validator(mode="before")
    @classmethod
    def _normalise_flat_keys(cls, data: Any) -> Any:
        """Lift flat x_m / y_m / theta_deg / map_id into a location dict."""
        if not isinstance(data, dict):
            return data
        if "location" in data:
            return data  # already nested
        # Check for flat coordinate keys
        loc_keys = {"x_m", "y_m", "theta_deg", "map_id"}
        found = {k: data[k] for k in loc_keys if k in data}
        if "x_m" in found and "y_m" in found:
            data = dict(data)  # copy to avoid mutation
            loc = {}
            for k in loc_keys:
                if k in data:
                    loc[k] = data.pop(k)
            data["location"] = loc
        return data


class GoToPoseRequest(BaseModel):
    x_m: float = Field(..., description="Target X coordinate in meters")
    y_m: float = Field(..., description="Target Y coordinate in meters")
    theta_deg: float = Field(0.0, description="Target heading in degrees")
    map_id: Optional[str | int] = Field(None, description="Map identifier")
    max_speed_m_s: Optional[float] = Field(None, description="Max speed, m/s")
    wait: bool = Field(True, description="Wait for transport completion")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "x_m": 5.0,
            "y_m": 3.2,
            "theta_deg": 180,
            "map_id": 2,
            "max_speed_m_s": 0.8,
            "wait": True
        }
    })



# ============================================================================
# КООРДИНИРОВАННЫЕ ОТВЕТЫ
# ============================================================================
class RobotActionResponse(BaseModel):
    success: bool = Field(..., description="True if command was executed successfully")
    task_id: Optional[str] = Field(None, description="Task id")
    result: Optional[Any] = Field(None, description="Result data, if available")
    detail: Optional[str] = Field(None, description="Human-readable description or message")
    error: Optional[str] = Field(None, description="Error message, if any")


class ApiError(BaseModel):
    """Standard FastAPI error envelope.

    All our errors are raised as HTTPException with detail=ErrorStatus.
    This model documents that shape for OpenAPI.
    """
    detail: ErrorStatus
