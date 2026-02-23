"""Object Dictionary (OD) helpers for CiA 402 / CANopen-over-gateway devices.

This package defines:
- Standard CiA 402 index constants (0x6040, 0x6041, ...)
- Primitive data types (INT8/UINT16/INT32, ...)
- Controlword and Statusword bit definitions + helper utilities

The goal is to centralize all OD-related knowledge in one place, so higher layers
(protocol, state machine, motion) can stay declarative and testable.
"""

from .controlword import (
    CWBit,
    cw_clear_bits,
    cw_disable_voltage,
    cw_enable_operation,
    cw_fault_reset,
    cw_quick_stop,
    cw_set_bits,
    cw_shutdown,
    cw_switch_on,
    cw_with_bit,
)
from .indices import ODIndex
from .statusword import (
    CiA402State,
    SWBit,
    decode_statusword,
    infer_cia402_state,
)
from .types import INT8, INT16, INT32, UINT8, UINT16, UINT32, ODType

__all__ = [
    # indices
    "ODIndex",
    # types
    "ODType",
    "INT8",
    "UINT8",
    "INT16",
    "UINT16",
    "INT32",
    "UINT32",
    # controlword
    "CWBit",
    "cw_shutdown",
    "cw_switch_on",
    "cw_enable_operation",
    "cw_disable_voltage",
    "cw_quick_stop",
    "cw_fault_reset",
    "cw_set_bits",
    "cw_clear_bits",
    "cw_with_bit",
    # statusword
    "SWBit",
    "CiA402State",
    "decode_statusword",
    "infer_cia402_state",
]
