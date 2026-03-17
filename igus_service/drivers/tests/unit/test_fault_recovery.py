"""Tests for FaultManager.reset_fault lifecycle.

Verifies: happy path (FAULT → cleared), timeout path, no-fault early return.
Uses mock OD accessor with controllable statusword responses.
"""

from __future__ import annotations

import pytest

from drivers.dryve_d1.cia402.fault import FaultManager, FaultResetError
from drivers.dryve_d1.od.statusword import SWBit


# FAULT statusword: bit 3 (FAULT) + bit 9 (REMOTE)
_SW_FAULT = (1 << SWBit.FAULT) | (1 << SWBit.REMOTE)  # 0x0208

# Healthy statusword: bits 0,1,2,5 + bit 9 (REMOTE) — OPERATION_ENABLED
_SW_HEALTHY = 0x0227


class SequenceOD:
    """OD accessor that returns statuswords from a sequence.

    First N read_u16 calls on STATUSWORD return from the sequence,
    then repeat the last value.
    """

    def __init__(self, statusword_sequence: list[int]) -> None:
        self._sw_seq = list(statusword_sequence)
        self._sw_idx = 0
        self.writes: list[tuple[int, int, int]] = []

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        from drivers.dryve_d1.od.indices import ODIndex
        if index == int(ODIndex.STATUSWORD):
            if self._sw_idx < len(self._sw_seq):
                val = self._sw_seq[self._sw_idx]
                self._sw_idx += 1
                return val
            return self._sw_seq[-1]
        return 0

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        return 0

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        return 0

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))


@pytest.mark.asyncio
async def test_fault_reset_happy_path() -> None:
    """FAULT → write fault_reset pulse → statusword clears FAULT bit → success."""
    od = SequenceOD([
        _SW_FAULT,    # initial read: fault detected
        _SW_HEALTHY,  # poll after reset: fault cleared
    ])
    fm = FaultManager(od)

    await fm.reset_fault(timeout_s=1.0, poll_interval_s=0.01)

    # Verify controlword writes happened (fault_reset pulse + shutdown + halt)
    assert len(od.writes) >= 2, f"Expected at least 2 writes, got {len(od.writes)}"


@pytest.mark.asyncio
async def test_fault_reset_timeout() -> None:
    """FAULT that never clears → FaultResetError after timeout."""
    od = SequenceOD([
        _SW_FAULT,  # initial read: fault detected
        _SW_FAULT,  # stays in fault after reset pulse
        _SW_FAULT,
        _SW_FAULT,
        _SW_FAULT,
    ])
    fm = FaultManager(od)

    with pytest.raises(FaultResetError, match="timed out"):
        await fm.reset_fault(timeout_s=0.05, poll_interval_s=0.01)


@pytest.mark.asyncio
async def test_fault_reset_no_fault_early_return() -> None:
    """No fault present → early return, no controlword writes."""
    od = SequenceOD([_SW_HEALTHY])
    fm = FaultManager(od)

    await fm.reset_fault(timeout_s=1.0, poll_interval_s=0.01)

    assert len(od.writes) == 0, "Should not write controlword when no fault"
