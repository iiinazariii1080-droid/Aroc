"""Domain command objects for the application layer.

These plain dataclasses represent the *intent* of a caller and are the input
boundary of ``DriveUseCases``.  Using domain commands (instead of Pydantic HTTP
request models) keeps the application layer independent of the presentation
layer for both input and output.

Route handlers are responsible for mapping the incoming Pydantic request model
to the appropriate command before calling the use case.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MotionProfile:
    """Drive motion profile parameters (velocity / accel / decel in drive units)."""
    velocity: int
    acceleration: int
    deceleration: int


@dataclass(frozen=True)
class MoveCommand:
    """Command to move the drive to an absolute or relative position."""
    target_position: int
    relative: bool
    profile: MotionProfile
    timeout_ms: int = 20000


@dataclass(frozen=True)
class JogCommand:
    """Command to start or update a jog movement."""
    direction: Literal["positive", "negative"]
    speed: float | None     # drive units/s; None → service default
    ttl_ms: int             # watchdog TTL in milliseconds


@dataclass(frozen=True)
class StopCommand:
    """Command to stop the drive."""
    mode: Literal["quick_stop", "halt"] = "quick_stop"


@dataclass(frozen=True)
class ReferenceCommand:
    """Command to perform homing (reference) operation."""
    timeout_ms: int = 60000


@dataclass(frozen=True)
class FaultResetCommand:
    """Command to reset a drive fault."""
    auto_enable: bool = True
