from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MotionLimits:
    """Soft motion limits applied by the motion layer.

    Units are drive-specific (typically counts, counts/s, counts/s^2).
    This layer does not assume scaling; scaling should be handled by your API layer.

    If a field is None, it is not enforced.
    """

    max_position: int | None = None
    min_position: int | None = None

    max_velocity: int | None = None
    max_accel: int | None = None
    max_decel: int | None = None

    def clamp_position(self, pos: int) -> int:
        if self.min_position is not None and pos < self.min_position:
            return int(self.min_position)
        if self.max_position is not None and pos > self.max_position:
            return int(self.max_position)
        return int(pos)

    def clamp_velocity(self, vel: int) -> int:
        if self.max_velocity is None:
            return int(vel)
        mv = int(self.max_velocity)
        if vel > mv:
            return mv
        if vel < -mv:
            return -mv
        return int(vel)

    def clamp_accel(self, accel: int) -> int:
        if self.max_accel is None:
            return int(accel)
        return min(int(accel), int(self.max_accel))

    def clamp_decel(self, decel: int) -> int:
        if self.max_decel is None:
            return int(decel)
        return min(int(decel), int(self.max_decel))


def validate_limits(limits: MotionLimits) -> None:
    """Validate internal consistency of MotionLimits."""
    if limits.min_position is not None and limits.max_position is not None:
        if int(limits.min_position) > int(limits.max_position):
            raise ValueError("min_position cannot be greater than max_position")
    for name in ("max_velocity", "max_accel", "max_decel"):
        v = getattr(limits, name)
        if v is not None and int(v) < 0:
            raise ValueError(f"{name} must be non-negative or None")
