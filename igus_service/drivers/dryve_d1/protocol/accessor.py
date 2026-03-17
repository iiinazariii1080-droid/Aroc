"""Canonical AsyncODAccessor Protocol for the dryve D1 driver.

All driver sub-modules (cia402, motion, telemetry) share one definition
rather than each declaring their own local copy.  Import this from
wherever async OD access is needed:

    from ..protocol.accessor import AsyncODAccessor
"""

from __future__ import annotations

from typing import Protocol


class AsyncODAccessor(Protocol):
    """Minimal async Object-Dictionary accessor required by driver internals.

    Every method corresponds to a typed SDO read or write.  The suffix
    encodes the CiA 402 data type: u8/u16/u32 = unsigned, i8/i32 = signed.
    All integers use little-endian encoding as required by the dryve D1
    Modbus TCP Gateway telegram specification.
    """

    async def read_u16(self, index: int, subindex: int = 0) -> int: ...
    async def read_i8(self, index: int, subindex: int = 0) -> int: ...
    async def read_i32(self, index: int, subindex: int = 0) -> int: ...
    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None: ...
