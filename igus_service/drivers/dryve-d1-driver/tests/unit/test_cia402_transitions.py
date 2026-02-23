import asyncio
import pytest

from drivers.dryve_d1.cia402.state_machine import CiA402StateMachine, StateMachineConfig
from drivers.dryve_d1.od.statusword import CiA402State


def _sw_for(state: CiA402State) -> int:
    # Minimal patterns to satisfy infer_cia402_state() implementation
    # Bit 9 (REMOTE) must be set (0x0200) for state machine to work
    REMOTE_BIT = 0x0200
    if state == CiA402State.SWITCH_ON_DISABLED:
        return 0x0040 | REMOTE_BIT  # b6 + remote
    if state == CiA402State.READY_TO_SWITCH_ON:
        return 0x0021 | REMOTE_BIT  # b0 + b5 + remote
    if state == CiA402State.SWITCHED_ON:
        return 0x0023 | REMOTE_BIT  # b0 + b1 + b5 + remote
    if state == CiA402State.OPERATION_ENABLED:
        return 0x0027 | REMOTE_BIT  # b0 + b1 + b2 + b5 + remote
    if state == CiA402State.FAULT:
        return 0x0008 | REMOTE_BIT  # b3 + remote
    return REMOTE_BIT


class FakeOD:
    def __init__(self, initial: CiA402State):
        self.state = initial
        self.controlwords = []
        self.remote_enabled = True  # dominance checks use statusword bit9; state machine doesn't enforce remote here.

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        # Statusword only
        if index == 0x6041:
            return _sw_for(self.state)
        return 0

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        if index == 0x6040:
            self.controlwords.append(value & 0xFFFF)
            # update state based on canonical command words
            if (value & 0xFFFF) == 0x0006:       # shutdown
                self.state = CiA402State.READY_TO_SWITCH_ON
            elif (value & 0xFFFF) == 0x0007:     # switch on
                self.state = CiA402State.SWITCHED_ON
            elif (value & 0xFFFF) == 0x000F:     # enable operation
                self.state = CiA402State.OPERATION_ENABLED
            elif (value & 0xFFFF) == 0x0080:     # fault reset pulse
                # clear fault to switch on disabled, typical
                self.state = CiA402State.SWITCH_ON_DISABLED


@pytest.mark.asyncio
async def test_run_to_operation_enabled_from_switch_on_disabled():
    od = FakeOD(CiA402State.SWITCH_ON_DISABLED)
    sm = CiA402StateMachine(od, config=StateMachineConfig(poll_interval_s=0.0, step_timeout_s=0.5))
    await sm.run_to_operation_enabled()
    assert od.state == CiA402State.OPERATION_ENABLED
    # Should have written shutdown -> switch on -> enable op
    assert od.controlwords[:3] == [0x0006, 0x0007, 0x000F]


@pytest.mark.asyncio
async def test_fault_reset_pulses_and_clears():
    od = FakeOD(CiA402State.FAULT)
    sm = CiA402StateMachine(od, config=StateMachineConfig(poll_interval_s=0.0, step_timeout_s=0.5))
    await sm.fault_reset()
    assert 0x0080 in od.controlwords
    assert 0x0006 in od.controlwords  # shutdown after pulse
