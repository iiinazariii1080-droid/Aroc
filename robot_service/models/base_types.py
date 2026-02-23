"""
base_types.py - Base types and constants for typing standardization

This module contains:
1. Base type aliases for frequently used constructs
2. Validation constants (ranges, limits)
3. Common enums
4. Utility types for API

Use these types everywhere instead of using typing.* directly.
"""

from typing import (
    Dict, List, Optional, Union, Any, 
    TypeVar, Callable, Awaitable, Tuple
)
from pydantic import BaseModel, Field
from enum import Enum

# ============================================================================
# BASE TYPE ALIASES
# ============================================================================

# JSON-compatible types
JsonDict = Dict[str, Any]
JsonList = List[Any]
JsonValue = Union[str, int, float, bool, None, JsonDict, JsonList]

# API types
ApiResponse = JsonDict
ApiRequest = JsonDict
ApiError = JsonDict

# Specialized dicts
StringDict = Dict[str, str]
IntDict = Dict[str, int]
FloatDict = Dict[str, float]
BoolDict = Dict[str, bool]

# Lists
StringList = List[str]
IntList = List[int]
FloatList = List[float]
BoolList = List[bool]

# Optional types
OptionalStr = Optional[str]
OptionalInt = Optional[int]
OptionalFloat = Optional[float]
OptionalBool = Optional[bool]
OptionalDict = Optional[JsonDict]

# ============================================================================
# VALIDATION CONSTANTS
# ============================================================================

class VelocityLimits:
    """Velocity limits for all devices."""
    MIN_PERCENT = 1.0
    MAX_PERCENT = 100.0
    DEFAULT_PERCENT = 50.0

class PositionLimits:
    """Position limits for Igus."""
    MIN_CM = 0.0
    MAX_CM = 120.0
    DEFAULT_CM = 50.0

class ToolOffsetLimits:
    """xArm tool offset limits."""
    MIN_MM = -1000.0
    MAX_MM = 1000.0
    DEFAULT_MM = 0.0

class JointLimits:
    """xArm joint angle limits."""
    MIN_DEG = -500.0
    MAX_DEG = 500.0
    DEFAULT_DEG = 0.0

class CoordinateLimits:
    """AGV coordinate limits."""
    MIN_M = -1000.0
    MAX_M = 1000.0
    DEFAULT_M = 0.0

# ============================================================================
# ENUMS
# ============================================================================

class TaskStatus(str, Enum):
    """Async task statuses."""
    PENDING = "pending"
    WORKING = "working"
    FINISHED = "finished"
    ERROR = "error"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"

class DeviceState(str, Enum):
    """Device states."""
    OFFLINE = "offline"
    ONLINE = "online"
    ERROR = "error"
    BUSY = "busy"
    READY = "ready"

class MovementType(str, Enum):
    """Movement types."""
    LINEAR = "linear"
    JOINT = "joint"
    TOOL = "tool"
    POSE = "pose"

class ErrorLevel(str, Enum):
    """Error levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

# ============================================================================
# BASE MODELS
# ============================================================================

class BaseApiModel(BaseModel):
    """Base model for all API models."""
    class Config:
        # Use field aliases
        validate_by_name = True
        # Strict validation
        validate_assignment = True
        # Extra fields forbidden
        extra = "forbid"
        # Allow arbitrary types
        arbitrary_types_allowed = True

class BaseResponse(BaseApiModel):
    """Base model for all API responses."""
    success: bool = Field(..., description="Operation success")
    message: OptionalStr = Field(None, description="Result message")

class BaseError(BaseApiModel):
    """Base model for errors."""
    error: str = Field(..., description="Error text")
    code: OptionalInt = Field(None, description="Error code")
    details: OptionalDict = Field(None, description="Additional details")

class BaseAsyncResponse(BaseApiModel):
    """Base model for async responses."""
    success: bool = Field(..., description="Task start success")
    task_id: str = Field(..., description="Task identifier")

# ============================================================================
# UTILITY TYPES
# ============================================================================

# Function types
AsyncFunction = TypeVar('AsyncFunction', bound=Callable[..., Awaitable[Any]])
SyncFunction = TypeVar('SyncFunction', bound=Callable[..., Any])

# Coordinate types
Coordinate2D = Tuple[float, float]
Coordinate3D = Tuple[float, float, float]

# Result types
ResultT = TypeVar('ResultT')
ErrorT = TypeVar('ErrorT')
ResultOrError = Union[ResultT, ErrorT]

# ============================================================================
# SPECIALIZED TYPES
# ============================================================================



class VelocityPercent(float):
    """Type for velocity in percent with validation."""
    def __new__(cls, value: float) -> 'VelocityPercent':
        if not VelocityLimits.MIN_PERCENT <= value <= VelocityLimits.MAX_PERCENT:
            raise ValueError(f"Velocity must be between {VelocityLimits.MIN_PERCENT} and {VelocityLimits.MAX_PERCENT}")
        return super().__new__(cls, value)

class PositionCm(float):
    """Type for position in centimeters with validation."""
    def __new__(cls, value: float) -> 'PositionCm':
        if not PositionLimits.MIN_CM <= value <= PositionLimits.MAX_CM:
            raise ValueError(f"Position must be between {PositionLimits.MIN_CM} and {PositionLimits.MAX_CM}")
        return super().__new__(cls, value)

class ToolOffsetMm(float):
    """Type for tool offset in millimeters with validation."""
    def __new__(cls, value: float) -> 'ToolOffsetMm':
        if not ToolOffsetLimits.MIN_MM <= value <= ToolOffsetLimits.MAX_MM:
            raise ValueError(f"Tool offset must be between {ToolOffsetLimits.MIN_MM} and {ToolOffsetLimits.MAX_MM}")
        return super().__new__(cls, value)

class JointAngleDeg(float):
    """Type for joint angle in degrees with validation."""
    def __new__(cls, value: float) -> 'JointAngleDeg':
        if not JointLimits.MIN_DEG <= value <= JointLimits.MAX_DEG:
            raise ValueError(f"Joint angle must be between {JointLimits.MIN_DEG} and {JointLimits.MAX_DEG}")
        return super().__new__(cls, value)

# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Base types
    'JsonDict', 'JsonList', 'JsonValue',
    'ApiResponse', 'ApiRequest', 'ApiError',
    'StringDict', 'IntDict', 'FloatDict', 'BoolDict',
    'StringList', 'IntList', 'FloatList', 'BoolList',
    'OptionalStr', 'OptionalInt', 'OptionalFloat', 'OptionalBool', 'OptionalDict',
    
    # Constants
    'VelocityLimits', 'PositionLimits', 'ToolOffsetLimits', 'JointLimits', 'CoordinateLimits',
    
    # Enums
    'TaskStatus', 'DeviceState', 'MovementType', 'ErrorLevel',
    
    # Base models
    'BaseApiModel', 'BaseResponse', 'BaseError', 'BaseAsyncResponse',
    
    # Utility types
    'AsyncFunction', 'SyncFunction', 'Coordinate2D', 'Coordinate3D', 'ResultOrError',
    
    # Specialized types
    'VelocityPercent', 'PositionCm', 'ToolOffsetMm', 'JointAngleDeg',
] 