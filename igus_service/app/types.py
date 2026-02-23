from typing import Annotated

from pydantic import BaseModel, Field, StrictFloat


class PositionLimits:
    DEFAULT = 5000
    MIN = -120000
    MAX = 120000

class PercentLimits:
    DEFAULT = 100.0
    MIN = 0.1
    MAX = 100.0

class Parameters:
    position = Annotated[
        StrictFloat,
        Field(
            ge=PositionLimits.MIN,
            le=PositionLimits.MAX,
            default=PositionLimits.DEFAULT,
            description="Target position in motor units",
        ),
    ]
    percent = Annotated[
        StrictFloat,
        Field(
            ge=PercentLimits.MIN,
            le=PercentLimits.MAX,
            default=PercentLimits.DEFAULT,
            description="Value in percent of the drive's maximum",
        ),
    ]
    is_moving = Annotated[
        bool,
        Field(description="True if the motor is currently moving"),
    ]
    homed = Annotated[
        bool,
        Field(description="True if the axis has been homed"),
    ]
    connected = Annotated[
        bool,
        Field(description="True if connection to the drive is established"),
    ]
    error = Annotated[
        bool,
        Field(description="True if the drive is in error state"),
    ]
    blocking = Annotated[
        bool,
        Field(description="If true, API waits until motion completes"),
    ]
    statusWord = Annotated[
        int,
        Field(ge=0, le=0xFFFF, description="Drive status word (16-bit)"),
    ]

class MoveParams(BaseModel):
    position: Parameters.position
    velocity_percent: Parameters.percent
    acceleration_percent: Parameters.percent

class ActionResponse(BaseModel):
    success: bool
    error: str | None = None
    request_id: str | None = None
    command_id: str | None = None

class PositionResponse(BaseModel):
    position: Parameters.position

class MotionResponse(BaseModel):
    is_moving: Parameters.is_moving

class StatusResponse(BaseModel):
    status_word: Parameters.statusWord
    homed: Parameters.homed
    is_moving: Parameters.is_moving
    error: Parameters.error
    connected: Parameters.connected
    position: Parameters.position
    enabled: bool | None = Field(default=None, description="True if operation is enabled")
    last_error: str | None = Field(default=None, description="Last error message if any")