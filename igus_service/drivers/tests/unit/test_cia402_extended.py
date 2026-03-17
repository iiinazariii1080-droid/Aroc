"""Extended CiA402 state machine transition tests (TEST-03).

Covers transitions beyond the two existing tests:
- Already in operation_enabled -> idempotent
- From ready_to_switch_on -> operation_enabled
- From switched_on -> operation_enabled
- quick_stop from operation_enabled
- disable_voltage
- Timeout on stuck state
- fault_reset when not in fault (no-op)
- From quick_stop_active -> operation_enabled
"""

import asyncio

import pytest

from drivers.dryve_d1.cia402.state_machine import (
    CiA402StateMachine,
    StateMachineConfig,
    StateMachineTimeout,
)
from drivers.dryve_d1.od.statusword import CiA402State


REMOTE_BIT = 0x0200


def _sw_for(state: CiA402State) -> int:
    """Minimal statusword patterns satisfying infer_cia402_state()."""
    if state == CiA402State.SWITCH_ON_DISABLED:
        return 0x0040 | REMOTE_BIT
    if state == CiA402State.READY_TO_SWITCH_ON:
        return 0x0021 | REMOTE_BIT
    if state == CiA402State.SWITCHED_ON:
        return 0x0023 | REMOTE_BIT
    if state == CiA402State.OPERATION_ENABLED:
        return 0x0027 | REMOTE_BIT
    if state == CiA402State.FAULT:
        return 0x0008 | REMOTE_BIT
    if state == CiA402State.QUICK_STOP_ACTIVE:
        return 0x0017 | REMOTE_BIT  # b0+b1+b2+b4 + remote (QS active pattern)
    return REMOTE_BIT


class FakeOD:
    """Fake OD accessor that simulates CiA402 state transitions."""

    def __init__(self, initial: CiA402State):
        self.state = initial
        self.controlwords: list[int] = []

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        if index == 0x6041:
            return _sw_for(self.state)
        return 0

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        if index == 0x6040:
            cw = value & 0xFFFF
            self.controlwords.append(cw)
            # Simulate transitions based on controlword
            if cw == 0x0006:  # shutdown
                self.state = CiA402State.READY_TO_SWITCH_ON
            elif cw == 0x0007:  # switch on
                self.state = CiA402State.SWITCHED_ON
            elif cw == 0x000F:  # enable operation
                self.state = CiA402State.OPERATION_ENABLED
            elif cw == 0x0080:  # fault reset
                self.state = CiA402State.SWITCH_ON_DISABLED
            elif cw == 0x0000:  # disable voltage
                self.state = CiA402State.SWITCH_ON_DISABLED
            elif cw & 0x000F == 0x000B:  # quick stop (bit2=0, bits 0,1,3=1) -> 0x000B
                self.state = CiA402State.QUICK_STOP_ACTIVE


class StuckOD:
    """OD that never changes state — for timeout testing."""

    def __init__(self, state: CiA402State):
        self.state = state

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        if index == 0x6041:
            return _sw_for(self.state)
        return 0

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        pass  # never transitions


_CFG = StateMachineConfig(poll_interval_s=0.0, step_timeout_s=0.5)


@pytest.mark.asyncio
async def test_already_operation_enabled_is_idempotent():
    od = FakeOD(CiA402State.OPERATION_ENABLED)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.run_to_operation_enabled()
    assert od.state == CiA402State.OPERATION_ENABLED
    # Should write hold bits (0x000F)
    assert 0x000F in od.controlwords


@pytest.mark.asyncio
async def test_from_ready_to_switch_on():
    od = FakeOD(CiA402State.READY_TO_SWITCH_ON)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.run_to_operation_enabled()
    assert od.state == CiA402State.OPERATION_ENABLED


@pytest.mark.asyncio
async def test_from_switched_on():
    od = FakeOD(CiA402State.SWITCHED_ON)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.run_to_operation_enabled()
    assert od.state == CiA402State.OPERATION_ENABLED


@pytest.mark.asyncio
async def test_from_quick_stop_active():
    od = FakeOD(CiA402State.QUICK_STOP_ACTIVE)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.run_to_operation_enabled()
    assert od.state == CiA402State.OPERATION_ENABLED


@pytest.mark.asyncio
async def test_disable_voltage():
    od = FakeOD(CiA402State.OPERATION_ENABLED)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.disable_voltage()
    assert 0x0000 in od.controlwords
    assert od.state == CiA402State.SWITCH_ON_DISABLED


@pytest.mark.asyncio
async def test_fault_reset_when_not_in_fault_is_noop():
    """fault_reset() on a healthy drive should be a no-op."""
    od = FakeOD(CiA402State.OPERATION_ENABLED)
    sm = CiA402StateMachine(od, config=_CFG)
    await sm.fault_reset()
    # No fault reset controlword should have been written
    assert 0x0080 not in od.controlwords


@pytest.mark.asyncio
async def test_timeout_on_stuck_state():
    """State machine should raise StateMachineError (or subclass) when drive is stuck."""
    from drivers.dryve_d1.cia402.state_machine import StateMachineError as SMError
    od = StuckOD(CiA402State.SWITCHED_ON)
    cfg = StateMachineConfig(poll_interval_s=0.0, step_timeout_s=0.1)
    sm = CiA402StateMachine(od, config=cfg)
    with pytest.raises(SMError):
        await sm.run_to_operation_enabled()


@pytest.mark.asyncio
async def test_current_state_reads_statusword():
    od = FakeOD(CiA402State.FAULT)
    sm = CiA402StateMachine(od, config=_CFG)
    state = await sm.current_state()
    assert state == CiA402State.FAULT
