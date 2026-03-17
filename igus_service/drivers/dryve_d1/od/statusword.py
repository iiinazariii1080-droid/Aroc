"""CiA 402 Statusword (0x6041) decoding and CiA 402 state inference.

Statusword bits are used to:
- infer the current CiA 402 device state (Not ready, Switch on disabled, Ready, Switched on, Operation enabled, etc.)
- check for faults/warnings and mode-specific status conditions
- detect completion conditions (e.g., target reached in profile position)

This module provides:
- Bit enums
- `decode_statusword` -> dict of named flags
- `infer_cia402_state` -> CiA402State enum

Implementation note:
CiA 402 defines state patterns over bits 0..6 and 3 (fault) and some additional bits.
We implement the standard pattern table. Vendor-specific deviations should be handled
in a higher layer (cia402/fault.py or cia402/dominance.py).
"""

from __future__ import annotations

from enum import Enum, IntEnum

from ..cia402.bits import bit_is_set as _bit


class SWBit(IntEnum):
    READY_TO_SWITCH_ON = 0
    SWITCHED_ON = 1
    OPERATION_ENABLED = 2
    FAULT = 3
    VOLTAGE_ENABLED = 4
    QUICK_STOP = 5
    SWITCH_ON_DISABLED = 6
    WARNING = 7
    REMOTE = 9            # often used as "remote"; some drives use bit 8 as manufacturer-specific
    TARGET_REACHED = 10   # frequently used in profile position
    INTERNAL_LIMIT_ACTIVE = 11
    OP_MODE_SPECIFIC = 12
    FOLLOWING_ERROR = 13  # some drives use 13/14; keep as optional hint


class CiA402State(str, Enum):
    NOT_READY_TO_SWITCH_ON = "not_ready_to_switch_on"
    SWITCH_ON_DISABLED = "switch_on_disabled"
    READY_TO_SWITCH_ON = "ready_to_switch_on"
    SWITCHED_ON = "switched_on"
    OPERATION_ENABLED = "operation_enabled"
    QUICK_STOP_ACTIVE = "quick_stop_active"
    FAULT_REACTION_ACTIVE = "fault_reaction_active"
    FAULT = "fault"
    UNKNOWN = "unknown"


def decode_statusword(word: int) -> dict[str, bool]:
    """Decode commonly used statusword bits into a boolean dict."""
    w = int(word) & 0xFFFF
    flags: dict[str, bool] = {
        "ready_to_switch_on": _bit(w, SWBit.READY_TO_SWITCH_ON),
        "switched_on": _bit(w, SWBit.SWITCHED_ON),
        "operation_enabled": _bit(w, SWBit.OPERATION_ENABLED),
        "fault": _bit(w, SWBit.FAULT),
        "voltage_enabled": _bit(w, SWBit.VOLTAGE_ENABLED),
        "quick_stop": _bit(w, SWBit.QUICK_STOP),
        "switch_on_disabled": _bit(w, SWBit.SWITCH_ON_DISABLED),
        "warning": _bit(w, SWBit.WARNING),
        "remote": _bit(w, SWBit.REMOTE),
        "target_reached": _bit(w, SWBit.TARGET_REACHED),
        "internal_limit_active": _bit(w, SWBit.INTERNAL_LIMIT_ACTIVE),
        "op_mode_specific": _bit(w, SWBit.OP_MODE_SPECIFIC),
        "following_error": _bit(w, SWBit.FOLLOWING_ERROR),
    }
    return flags


def infer_cia402_state(statusword: int) -> CiA402State:
    """Infer CiA 402 state from statusword using the standard bit-pattern table.

    CiA 402 pattern uses bits:
      b0 Ready to switch on
      b1 Switched on
      b2 Operation enabled
      b3 Fault
      b5 Quick stop
      b6 Switch on disabled

    We follow the canonical state mapping:
      - Not ready to switch on: b0=0 b1=0 b2=0 b3=0 b6=0
      - Switch on disabled:     b0=0 b1=0 b2=0 b3=0 b6=1
      - Ready to switch on:     b0=1 b1=0 b2=0 b3=0 b6=0
      - Switched on:            b0=1 b1=1 b2=0 b3=0 b6=0
      - Operation enabled:      b0=1 b1=1 b2=1 b3=0 b6=0
      - Quick stop active:      b0=1 b1=1 b2=1 b3=0 b5=0 b6=0  (common variant)
      - Fault reaction active:  b0=1 b1=1 b2=1 b3=1 b6=0 (often transient)
      - Fault:                  b3=1 and other bits per table

    Drives sometimes vary in Quick stop pattern; treat UNKNOWN carefully.
    """
    w = int(statusword) & 0xFFFF
    b0 = _bit(w, 0)
    b1 = _bit(w, 1)
    b2 = _bit(w, 2)
    b3 = _bit(w, 3)
    b5 = _bit(w, 5)
    b6 = _bit(w, 6)

    if b3 and b0 and b1 and b2:
        # often fault reaction active (transient) or fault; keep separate if possible
        return CiA402State.FAULT_REACTION_ACTIVE

    # Fault (many drives show b3=1 regardless of others)
    if b3:
        return CiA402State.FAULT

    # Not ready / disabled / ready / switched on / enabled
    if (not b0) and (not b1) and (not b2) and (not b6):
        return CiA402State.NOT_READY_TO_SWITCH_ON
    if (not b0) and (not b1) and (not b2) and b6:
        return CiA402State.SWITCH_ON_DISABLED
    if b0 and (not b1) and (not b2) and (not b6):
        return CiA402State.READY_TO_SWITCH_ON
    if b0 and b1 and (not b2) and (not b6):
        return CiA402State.SWITCHED_ON
    if b0 and b1 and b2 and (not b6):
        # Quick stop active is commonly indicated by b5=0 while enabled bits remain set.
        if not b5:
            return CiA402State.QUICK_STOP_ACTIVE
        return CiA402State.OPERATION_ENABLED

    return CiA402State.UNKNOWN
