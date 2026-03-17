"""Fault decode and reset routines for dryve D1.

Manual highlights:
- If an error is detected, Statusword bit 3 ('Fault') is set.
- Errors can be reset by setting Controlword bit 7 ('Fault Reset'), but only if
  DI7 'Enable' is HIGH (Statusword bit 9 'Remote' == 1).
- After an error during a movement is reset, movement can only be restarted after:
    * Controlword bit 8 'Halt' has been set HIGH, and
    * the state machine is run through again to 'Operation enabled'
  (The safest approach is to recover into Operation Enabled with HALT=1, and let
  motion layers explicitly clear HALT when starting a movement.)

The drive exposes diagnostic objects such as:
- 0x603F Error Code (commonly UNSIGNED16)
- 0x1001 Error Register
- 0x1003 Pre-defined Error Field (error history)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

from ..od.controlword import CWBit, cw_fault_reset, cw_set_bits, cw_shutdown
from ..od.indices import ODIndex
from ..od.statusword import SWBit
from ..protocol.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .bits import _U16_MASK
from .bits import bit_is_set as _bit
from .dominance import require_remote_enabled

# Standard CANopen diagnostic objects frequently present on drives
OD_ERROR_REGISTER = 0x1001
OD_PREDEFINED_ERROR_FIELD = 0x1003


class FaultResetError(RuntimeError):
    """Raised when a fault reset attempt fails or times out."""


@dataclass(frozen=True, slots=True)
class FaultInfo:
    statusword: int
    error_code: int | None = None
    error_register: int | None = None
    history: list[int] | None = None

    def as_dict(self) -> dict:
        return {
            "statusword": f"0x{int(self.statusword) & _U16_MASK:04X}",
            "error_code": None if self.error_code is None else f"0x{int(self.error_code) & _U16_MASK:04X}",
            "error_register": None if self.error_register is None else f"0x{int(self.error_register) & 0xFF:02X}",
            "history": None if self.history is None else [f"0x{int(x) & _U16_MASK:04X}" for x in self.history],
        }


class FaultManager:
    """Reads fault diagnostics and performs a fault reset sequence."""

    def __init__(self, od: AsyncODAccessor) -> None:
        self._od = od

    async def read_statusword(self) -> int:
        return int(await self._od.read_u16(int(ODIndex.STATUSWORD), 0)) & _U16_MASK

    async def read_error_code(self) -> int:
        return int(await self._od.read_u16(int(ODIndex.ERROR_CODE), 0)) & _U16_MASK

    async def read_error_register(self) -> int:
        # often UINT8; we read as u16 and mask for simplicity
        return int(await self._od.read_u16(OD_ERROR_REGISTER, 0)) & 0x00FF

    async def read_error_history(self, *, max_entries: int = 8) -> list[int]:
        """Read Pre-defined Error Field (0x1003) if present.

        Subindex 0 usually contains the number of errors stored (UINT8),
        followed by error codes in subindices 1..N.
        """
        try:
            count = int(await self._od.read_u16(OD_PREDEFINED_ERROR_FIELD, 0)) & 0x00FF
        except (TimeoutError, OSError, ConnectionError):
            _LOGGER.debug("Error history unavailable (connection issue)", exc_info=True)
            return []
        except Exception:
            _LOGGER.warning("Unexpected error reading error history count", exc_info=True)
            return []
        count = max(0, min(int(count), int(max_entries)))
        hist: list[int] = []
        for si in range(1, count + 1):
            try:
                hist.append(int(await self._od.read_u16(OD_PREDEFINED_ERROR_FIELD, si)) & _U16_MASK)
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error history entry %d unavailable (connection issue)", si, exc_info=True)
                break
            except Exception:
                _LOGGER.warning("Unexpected error reading error history entry %d", si, exc_info=True)
                break
        return hist

    async def read_fault_info(self, *, include_history: bool = True) -> FaultInfo:
        sw = await self.read_statusword()
        info = FaultInfo(statusword=sw)
        if _bit(sw, SWBit.FAULT):
            # Best-effort reads; don't let diagnostics hide original fault
            try:
                ec = await self.read_error_code()
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error code unavailable (connection issue)", exc_info=True)
                ec = None
            except Exception:
                _LOGGER.warning("Unexpected error reading error code", exc_info=True)
                ec = None
            try:
                er = await self.read_error_register()
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error register unavailable (connection issue)", exc_info=True)
                er = None
            except Exception:
                _LOGGER.warning("Unexpected error reading error register", exc_info=True)
                er = None
            hist = None
            if include_history:
                try:
                    hist_list = await self.read_error_history()
                    hist = hist_list if hist_list else None
                except (TimeoutError, OSError, ConnectionError):
                    _LOGGER.debug("Error history unavailable (connection issue)", exc_info=True)
                    hist = None
                except Exception:
                    _LOGGER.warning("Unexpected error reading error history", exc_info=True)
                    hist = None
            return FaultInfo(statusword=sw, error_code=ec, error_register=er, history=hist)
        return info

    async def reset_fault(self, *, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None:
        """Attempt to reset a fault (standalone, without state machine context).

        Sequence (safe baseline):
        - Verify Remote enabled (DI7 high), otherwise reset is not permitted.
        - Write Controlword bit7 (Fault reset).
        - Pulse/clear by sending Shutdown.
        - Set HALT=1 (safe) after reset, per manual guidance for restarting after faults.
        - Wait until Statusword bit3 clears or timeout.

        Note: Production fault_reset flow uses ``CiA402StateMachine.fault_reset()``
        (state_machine.py), which integrates with the CiA402 state transition logic.
        This method is a lower-level alternative for direct FaultManager usage.
        """
        sw0 = await self.read_statusword()
        if not _bit(sw0, SWBit.FAULT):
            return
        require_remote_enabled(sw0)

        # issue fault reset pulse
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(cw_fault_reset()) & _U16_MASK, 0)
        await asyncio.sleep(poll_interval_s)

        # clear to shutdown baseline
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(cw_shutdown()) & _U16_MASK, 0)

        # after a fault, ensure HALT=1 when we later go to op enabled
        safe_halt_shutdown = cw_set_bits(cw_shutdown(), CWBit.HALT)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(safe_halt_shutdown) & _U16_MASK, 0)

        deadline = monotonic_s() + float(timeout_s)
        while True:
            sw = await self.read_statusword()
            if not _bit(sw, SWBit.FAULT):
                return
            if monotonic_s() >= deadline:
                raise FaultResetError(f"Fault reset timed out; statusword=0x{sw:04X}")
            await asyncio.sleep(poll_interval_s)
