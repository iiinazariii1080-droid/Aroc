from typing import Annotated

from pydantic import BaseModel, Field


class PercentLimits:
    DEFAULT = 100.0
    MIN = 0.1
    MAX = 100.0


# Module-level type aliases (replaces Parameters namespace class)
#
# Lower bound (ge=0) is enforced here — the dryve D1 position range is
# always non-negative.  Upper bound is NOT enforced here — the driver
# validates against DRYVE_MAX_POSITION_LIMIT at command execution time,
# so both legacy and v1 APIs behave identically regardless of operator
# configuration.
PositionParam = Annotated[
    int,
    Field(
        ge=0,
        description="Target position in motor units (integer drive units)",
    ),
]
PercentParam = Annotated[
    float,
    Field(
        ge=PercentLimits.MIN,
        le=PercentLimits.MAX,
        default=PercentLimits.DEFAULT,
        description="Value in percent of the drive's maximum",
    ),
]
IsMovingParam = Annotated[
    bool,
    Field(description="True if the motor is currently moving"),
]
HomedParam = Annotated[
    bool,
    Field(description="True if the axis has been homed"),
]
ConnectedParam = Annotated[
    bool,
    Field(description="True if connection to the drive is established"),
]
ErrorParam = Annotated[
    bool,
    Field(description="True if the drive is in error state"),
]
StatusWordParam = Annotated[
    int,
    Field(ge=0, le=0xFFFF, description="Drive status word (16-bit)"),
]


class MoveParams(BaseModel):
    position: PositionParam
    velocity_percent: PercentParam
    acceleration_percent: PercentParam

class ActionResponse(BaseModel):
    success: bool
    error: str | None = None
    request_id: str | None = None
    command_id: str | None = None

class PositionResponse(BaseModel):
    # Response model: no bounds validation — hardware position can exceed soft limits.
    position: float = Field(..., description="Current position in drive units")

class MotionResponse(BaseModel):
    is_moving: IsMovingParam

class StatusResponse(BaseModel):
    status_word: StatusWordParam
    homed: HomedParam
    is_moving: IsMovingParam
    error: ErrorParam
    connected: ConnectedParam
    # Response model: no bounds validation — hardware position can exceed soft limits.
    position: float = Field(..., description="Current position in drive units")
    enabled: bool | None = Field(default=None, description="True if operation is enabled")
    last_error: str | None = Field(default=None, description="Last error message if any")
