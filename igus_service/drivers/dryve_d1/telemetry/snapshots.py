from __future__ import annotations

from dataclasses import dataclass

from ..od.statusword import CiA402State


@dataclass(frozen=True, slots=True)
class DriveSnapshot:
    """A single telemetry sample of the drive state.

    All numeric fields are raw CiA 402 values (after SDO decode), i.e.:
    - statusword: uint16
    - position: int32 (Position actual value 0x6064)
    - velocity: int32 (Velocity actual value 0x606C)
    - mode_display: int8 (Modes of operation display 0x6061)
    """

    ts_monotonic_s: float

    statusword: int
    cia402_state: CiA402State

    position: int | None = None
    velocity: int | None = None
    mode_display: int | None = None

    decoded_status: dict[str, bool] | None = None
