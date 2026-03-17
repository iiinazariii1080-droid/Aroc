"""Shared bit-manipulation utilities for CiA 402 word decoding."""

from __future__ import annotations

_U16_MASK = 0xFFFF


def bit_is_set(word: int, bit: int) -> bool:
    """Return True if *bit* is set in the unsigned-16 *word*."""
    return bool(((int(word) & _U16_MASK) >> int(bit)) & 1)
