"""CiA 402 Controlword (0x6040) bit definitions and helpers.

Controlword is used to:
- Drive CiA 402 state machine transitions (Shutdown, Switch On, Enable Operation, Fault Reset, etc.)
- Trigger set-point processing for profile position / velocity / homing modes
- Control HALT behavior in some modes

This module provides:
- Bit enums
- Canonical CiA 402 command words
- Convenience helpers for setting/clearing bits on an existing word

Important:
Different drives may implement additional semantics (especially for bits 4..6 / 8).
Keep vendor-specific logic out of this file.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum


class CWBit(IntEnum):
    SWITCH_ON = 0          # bit 0
    ENABLE_VOLTAGE = 1     # bit 1
    QUICK_STOP = 2         # bit 2 (1 = quick stop ENABLED, 0 = quick stop ACTIVE)
    ENABLE_OPERATION = 3   # bit 3
    NEW_SET_POINT = 4      # bit 4 (profile position: latch new set-point)
    CHANGE_SET_IMMEDIATELY = 5  # bit 5 (profile position: immediate)
    ABS_REL = 6            # bit 6 (profile position: absolute/relative)
    FAULT_RESET = 7        # bit 7
    HALT = 8               # bit 8 (profile modes: halt)


def _mask(bits: Iterable[int]) -> int:
    m = 0
    for b in bits:
        m |= 1 << int(b)
    return m


def cw_set_bits(word: int, *bits: CWBit) -> int:
    """Return word with given bits set."""
    return int(word) | _mask(bits)


def cw_clear_bits(word: int, *bits: CWBit) -> int:
    """Return word with given bits cleared."""
    return int(word) & ~_mask(bits)


def cw_with_bit(word: int, bit: CWBit, enabled: bool) -> int:
    """Set/clear a single bit."""
    return cw_set_bits(word, bit) if enabled else cw_clear_bits(word, bit)


# --- Canonical CiA 402 command words (lower 4 bits + fault reset) ---
# These values are widely used across CiA 402 implementations.

def cw_disable_voltage() -> int:
    """Disable voltage (also 'switch on disabled' request in many drives)."""
    return 0x0000


def cw_shutdown() -> int:
    """Shutdown: move towards 'Ready to switch on' (typical 0x0006)."""
    # enable voltage + quick stop, but not switch on / not enable operation
    return 0x0006


def cw_switch_on() -> int:
    """Switch on: move towards 'Switched on' (typical 0x0007)."""
    return 0x0007


def cw_enable_operation() -> int:
    """Enable operation: move towards 'Operation enabled' (typical 0x000F)."""
    return 0x000F


def cw_quick_stop(base: int = 0x000F) -> int:
    """CiA402 quick stop: clear bit 2 (QUICK_STOP) while maintaining hold bits.

    Per CiA402 standard, quick stop is triggered by clearing bit 2
    while keeping bits 0, 1, 3 (hold bits) set.

    Args:
        base: Base controlword value (default 0x000F = Operation Enabled)

    Returns:
        Controlword with bit 2 cleared (quick stop active).
    """
    return cw_clear_bits(base, CWBit.QUICK_STOP)


def cw_fault_reset() -> int:
    """Fault reset (bit 7). Usually 0x0080, sometimes combined with shutdown."""
    return 0x0080


# --- Helpers for profile modes ---

def cw_pulse_new_set_point(base: int) -> tuple[int, int]:
    """Return (set_word, clear_word) to pulse NEW_SET_POINT bit.

    Many drives require a rising edge on bit 4 to accept a new set-point.
    Callers should:
        write set_word, then write clear_word.
    """
    set_word = cw_set_bits(base, CWBit.NEW_SET_POINT)
    clear_word = cw_clear_bits(set_word, CWBit.NEW_SET_POINT)
    return set_word, clear_word
