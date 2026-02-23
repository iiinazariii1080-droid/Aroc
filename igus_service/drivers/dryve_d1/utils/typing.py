"""Shared typing aliases used across the driver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

# OD addresses
Index = NewType("Index", int)
SubIndex = NewType("SubIndex", int)
ODAddress = tuple[Index, SubIndex]

# Time units (semantic typing)
Seconds = NewType("Seconds", float)
Millis = NewType("Millis", int)


@dataclass(frozen=True, slots=True)
class RangeI:
    """Inclusive integer range."""
    lo: int
    hi: int

    def clamp(self, v: int) -> int:
        if v < self.lo:
            return self.lo
        if v > self.hi:
            return self.hi
        return v

    def contains(self, v: int) -> bool:
        return self.lo <= v <= self.hi
