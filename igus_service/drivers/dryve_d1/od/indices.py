"""Standard CiA 402 Object Dictionary indices used by the driver.

Note:
- These are the canonical indices defined by CiA 402 (and widely supported).
- Your drive may expose additional vendor-specific objects; add them here as needed.

We keep this file intentionally conservative: only the objects actually used by
the driver layers should be declared to avoid “constant sprawl”.
"""

from __future__ import annotations

from enum import IntEnum


class ODIndex(IntEnum):
    # --- CiA 402 core ---
    CONTROLWORD = 0x6040
    STATUSWORD = 0x6041

    MODES_OF_OPERATION = 0x6060
    MODES_OF_OPERATION_DISPLAY = 0x6061

    POSITION_ACTUAL_VALUE = 0x6064
    VELOCITY_ACTUAL_VALUE = 0x606C  # actual velocity (often signed)

    TARGET_POSITION = 0x607A
    TARGET_VELOCITY = 0x60FF

    PROFILE_VELOCITY = 0x6081
    PROFILE_ACCELERATION = 0x6083
    PROFILE_DECELERATION = 0x6084
    QUICK_STOP_DECELERATION = 0x6085

    # --- Homing (if supported) ---
    HOMING_METHOD = 0x6098
    HOMING_SPEEDS = 0x6099          # typically subindex 1/2
    HOMING_ACCELERATION = 0x609A

    # --- Diagnostics / errors ---
    ERROR_CODE = 0x603F             # standard error code
    MANUFACTURER_STATUS_REGISTER = 0x1002  # often present, optional

    # --- Optional: following error / limits ---
    FOLLOWING_ERROR_ACTUAL_VALUE = 0x60F4  # optional
    
    # --- Software position limits (CiA 402) ---
    MIN_POSITION_LIMIT = 0x607B  # Min position limit (INT32, RW)
    MAX_POSITION_LIMIT = 0x607D  # Max position limit (INT32, RW)
    
    # --- Vendor-specific (dryve D1) ---
    HOMING_STATUS = 0x2014  # dryve D1 specific: homing status (UINT16, RO)