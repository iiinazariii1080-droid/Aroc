"""OD (Object Dictionary) read/write mixin for DryveD1.

Provides low-level SDO read/write methods and the async _transceive bridge
that offloads blocking Modbus I/O to a thread pool executor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import SDOClient
    from ..transport import ModbusSession

_LOGGER_MODBUS = logging.getLogger("dryve_d1.modbus")


class OdAccessorMixin:
    """Async OD accessor: read/write integers of various widths via SDO."""

    # Provided by DryveD1.__init__
    _sdo: SDOClient
    _session: ModbusSession | None
    _modbus_executor: object  # concurrent.futures.ThreadPoolExecutor
    _modbus_io_timeout_s: float

    def _next_tid(self) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")
        return self._session.next_transaction_id()

    async def _transceive(self, adu: bytes) -> bytes:
        if self._session is None:
            raise RuntimeError("Not connected")

        session = self._session  # capture before crossing thread boundary
        _LOGGER_MODBUS.debug("_transceive: %d bytes", len(adu))
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(
            self._modbus_executor,
            session.transceive,
            adu,
        )
        return await asyncio.wait_for(fut, timeout=self._modbus_io_timeout_s)

    # ---- read helpers ----

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=2, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = self._sdo.decode_read_int(resp, request=req, signed=False) & 0xFFFF
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=2 u16 -> %d (0x%04X)", index, subindex, value, value)
        return value

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=4, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=True))
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=4 i32 -> %d", index, subindex, value)
        return value

    async def read_u32(self, index: int, subindex: int = 0) -> int:
        """Read UINT32 value from Object Dictionary."""
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=4, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=False))
        value = value & 0xFFFFFFFF
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=4 u32 -> %d", index, subindex, value)
        return value

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=1, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=True))
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=1 i8  -> %d", index, subindex, value)
        return value

    # ---- write helpers ----

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=2 u16 value=%d (0x%04X)", index, subindex, value, value & 0xFFFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=2, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=4 u32 value=%d", index, subindex, value & 0xFFFFFFFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=4, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=4 i32 value=%d", index, subindex, value)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=4, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=1 u8  value=%d (0x%02X)", index, subindex, value, value & 0xFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=1, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)
