"""Primitive OD types and encode/decode utilities.

We represent OD primitive types as lightweight descriptors (size, signedness, struct format).
Protocol layer can use these descriptors to consistently pack/unpack SDO values.

Design constraints:
- Deterministic, no side effects.
- Explicit about byte width and signedness.
- Uses little-endian by default (typical for CANopen value encoding),
  but protocol layer may override if the gateway requires a specific endian.

If your gateway or drive requires different encoding (e.g., big-endian),
adapt `ODType.pack/unpack` in one place.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

IntLike = int | bool


@dataclass(frozen=True, slots=True)
class ODType:
    """Descriptor for a primitive OD type."""

    name: str
    size: int  # bytes
    signed: bool
    fmt_le: str  # struct format character for little-endian packing, without endian prefix

    def pack(self, value: IntLike, *, endian: str = "<") -> bytes:
        """Pack an integer-like value into bytes.

        Args:
            value: int/bool
            endian: '<' little-endian, '>' big-endian

        Returns:
            bytes of length `self.size`
        """
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int):
            raise TypeError(f"{self.name}.pack expects int/bool, got {type(value).__name__}")
        try:
            return struct.pack(endian + self.fmt_le, value)
        except struct.error as e:
            raise ValueError(f"Value {value} does not fit {self.name}") from e

    def unpack(self, data: bytes, *, endian: str = "<") -> int:
        """Unpack bytes into int.

        Args:
            data: bytes-like of exact length `self.size`
            endian: '<' little-endian, '>' big-endian

        Returns:
            int
        """
        if len(data) != self.size:
            raise ValueError(f"{self.name}.unpack expects {self.size} bytes, got {len(data)}")
        return int(struct.unpack(endian + self.fmt_le, data)[0])

    def clamp(self, value: int) -> int:
        """Clamp an integer into the representable range of this type."""
        bits = self.size * 8
        if self.signed:
            lo = -(1 << (bits - 1))
            hi = (1 << (bits - 1)) - 1
        else:
            lo = 0
            hi = (1 << bits) - 1
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value

    def range(self) -> tuple[int, int]:
        """Return (min, max) representable range."""
        bits = self.size * 8
        if self.signed:
            return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
        return (0, (1 << bits) - 1)


# --- Common primitives used by CiA 402 ---
INT8 = ODType(name="INT8", size=1, signed=True, fmt_le="b")
UINT8 = ODType(name="UINT8", size=1, signed=False, fmt_le="B")

INT16 = ODType(name="INT16", size=2, signed=True, fmt_le="h")
UINT16 = ODType(name="UINT16", size=2, signed=False, fmt_le="H")

INT32 = ODType(name="INT32", size=4, signed=True, fmt_le="i")
UINT32 = ODType(name="UINT32", size=4, signed=False, fmt_le="I")
