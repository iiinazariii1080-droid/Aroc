"""Motion layer: homing, profile position, profile velocity, jog.

This package is intentionally transport-agnostic. It operates on a minimal
async OD accessor protocol (read/write primitive OD objects) and focuses on
CiA 402 motion semantics.

Modules:
- homing: Homing mode (6060=6)
- profile_position: Profile Position mode (6060=1)
- profile_velocity: Profile Velocity mode (6060=3)
- jog: Hold-to-move built on profile velocity with TTL watchdog support
"""

from .homing import Homing, HomingConfig, HomingResult
from .jog import JogConfig, JogController, JogState
from .profile_position import ProfilePosition, ProfilePositionConfig
from .profile_velocity import ProfileVelocity, ProfileVelocityConfig

__all__ = [
    "Homing",
    "HomingConfig",
    "HomingResult",
    "JogConfig",
    "JogController",
    "JogState",
    "ProfilePosition",
    "ProfilePositionConfig",
    "ProfileVelocity",
    "ProfileVelocityConfig",
]
