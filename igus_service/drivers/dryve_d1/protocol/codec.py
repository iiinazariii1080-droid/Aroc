from __future__ import annotations

from typing import Literal

Endian = Literal["<", ">"]


def _range_for(size: int, signed: bool) -> tuple[int, int]:
    if size not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported integer byte size: {size}")
    bits = size * 8
    if signed:
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)


def pack_int(value: int, *, size: int, signed: bool, endian: Endian) -> bytes:
    """Pack an integer into `size` bytes.

    - Uses two's complement for signed values.
    - Supports size 1..4.
    """
    lo, hi = _range_for(size, signed)
    if not isinstance(value, int):
        raise TypeError(f"pack_int expects int, got {type(value).__name__}")
    if value < lo or value > hi:
        raise ValueError(f"Value {value} out of range for {'I' if signed else 'U'}INT{size*8}: [{lo}, {hi}]")

    if signed and value < 0:
        value = (1 << (size * 8)) + value  # two's complement into unsigned range

    b = value.to_bytes(size, byteorder=("little" if endian == "<" else "big"), signed=False)
    return b


def unpack_int(data: bytes, *, signed: bool, endian: Endian) -> int:
    """Unpack an integer from bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"unpack_int expects bytes, got {type(data).__name__}")
    size = len(data)
    if size not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported integer byte size: {size}")

    unsigned = int.from_bytes(data, byteorder=("little" if endian == "<" else "big"), signed=False)
    if not signed:
        return unsigned

    sign_bit = 1 << (size * 8 - 1)
    if unsigned & sign_bit:
        return unsigned - (1 << (size * 8))
    return unsigned


# Convenience wrappers for common cases used by CiA402 objects
def pack_u16_le(value: int) -> bytes:
    return pack_int(value, size=2, signed=False, endian="<")


def unpack_u16_le(data: bytes) -> int:
    if len(data) != 2:
        raise ValueError("unpack_u16_le expects 2 bytes")
    return unpack_int(data, signed=False, endian="<")


def pack_i32_le(value: int) -> bytes:
    return pack_int(value, size=4, signed=True, endian="<")


def unpack_i32_le(data: bytes) -> int:
    if len(data) != 4:
        raise ValueError("unpack_i32_le expects 4 bytes")
    return unpack_int(data, signed=True, endian="<")
