"""Precondition ("dominance") checks for dryve D1 CiA 402 control.

The dryve D1 manual states that Digital Input DI7 "Enable" must be HIGH for the
state machine to run. The controller exposes this as Statusword bit 9 ("Remote").
If bit 9 is low, the drive will not proceed through the state machine.

This module is intentionally simple: it only validates what we can infer from SDOs.
Any hardware wiring / DI configuration is outside the scope of software checks.
"""

from __future__ import annotations

from ..od.statusword import SWBit
from .bits import _U16_MASK
from .bits import bit_is_set as _bit


class PreconditionFailed(RuntimeError):
    """Raised when a required drive precondition is not met."""


def require_remote_enabled(statusword: int) -> None:
    """Require Statusword bit 9 ('Remote') == 1.

    On dryve D1, this indicates DI7 'Enable' is logically HIGH.
    """
    if not _bit(statusword, SWBit.REMOTE):
        raise PreconditionFailed(
            "Remote not enabled: Statusword bit 9 is LOW (DI7 'Enable' must be HIGH). "
            f"statusword=0x{int(statusword) & _U16_MASK:04X}"
        )


def require_not_in_fault(statusword: int) -> None:
    """Require Statusword bit 3 ('Fault') == 0."""
    if _bit(statusword, SWBit.FAULT):
        raise PreconditionFailed(
            f"Drive in fault (Statusword bit 3 HIGH). statusword=0x{int(statusword) & _U16_MASK:04X}"
        )


def require_operation_enabled(statusword: int) -> None:
    """Require 'Operation enabled' bit == 1."""
    if not _bit(statusword, SWBit.OPERATION_ENABLED):
        raise PreconditionFailed(
            "Drive not in 'Operation enabled'. "
            f"statusword=0x{int(statusword) & _U16_MASK:04X}"
        )
