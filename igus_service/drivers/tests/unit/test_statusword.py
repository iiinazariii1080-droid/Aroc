"""Unit tests for statusword decoding and state inference."""

import pytest

from drivers.dryve_d1.od.statusword import (
    SWBit,
    CiA402State,
    decode_statusword,
    infer_cia402_state,
)


class TestSWBit:
    """Tests for SWBit enum."""

    def test_swbit_values(self):
        assert SWBit.READY_TO_SWITCH_ON == 0
        assert SWBit.SWITCHED_ON == 1
        assert SWBit.OPERATION_ENABLED == 2
        assert SWBit.FAULT == 3
        assert SWBit.VOLTAGE_ENABLED == 4
        assert SWBit.QUICK_STOP == 5
        assert SWBit.SWITCH_ON_DISABLED == 6
        assert SWBit.WARNING == 7
        assert SWBit.REMOTE == 9
        assert SWBit.TARGET_REACHED == 10


class TestDecodeStatusword:
    """Tests for decode_statusword function."""

    def test_decode_operation_enabled(self):
        sw = 0x0027
        decoded = decode_statusword(sw)
        assert decoded["ready_to_switch_on"] is True
        assert decoded["switched_on"] is True
        assert decoded["operation_enabled"] is True
        assert decoded["fault"] is False
        assert decoded["voltage_enabled"] is False
        assert decoded["quick_stop"] is True
        assert decoded["switch_on_disabled"] is False

    def test_decode_fault(self):
        sw = 0x0008
        decoded = decode_statusword(sw)
        assert decoded["fault"] is True
        assert decoded["operation_enabled"] is False

    def test_decode_switch_on_disabled(self):
        sw = 0x0040
        decoded = decode_statusword(sw)
        assert decoded["switch_on_disabled"] is True
        assert decoded["operation_enabled"] is False
        assert decoded["fault"] is False

    def test_decode_target_reached(self):
        sw = 0x0427
        decoded = decode_statusword(sw)
        assert decoded["target_reached"] is True
        assert decoded["operation_enabled"] is True

    def test_decode_remote(self):
        sw = 0x0227
        decoded = decode_statusword(sw)
        assert decoded["remote"] is True
        assert decoded["operation_enabled"] is True

    def test_decode_all_bits(self):
        sw = 0x3FFF
        decoded = decode_statusword(sw)
        assert decoded["ready_to_switch_on"] is True
        assert decoded["switched_on"] is True
        assert decoded["operation_enabled"] is True
        assert decoded["fault"] is True
        assert decoded["voltage_enabled"] is True
        assert decoded["quick_stop"] is True
        assert decoded["switch_on_disabled"] is True
        assert decoded["warning"] is True
        assert decoded["remote"] is True
        assert decoded["target_reached"] is True
        assert decoded["internal_limit_active"] is True
        assert decoded["op_mode_specific"] is True
        assert decoded["following_error"] is True

    def test_decode_zero(self):
        sw = 0x0000
        decoded = decode_statusword(sw)
        assert decoded["ready_to_switch_on"] is False
        assert decoded["switched_on"] is False
        assert decoded["operation_enabled"] is False
        assert decoded["fault"] is False
        assert decoded["target_reached"] is False


class TestInferCiA402State:
    """Tests for infer_cia402_state function."""

    def test_infer_operation_enabled(self):
        assert infer_cia402_state(0x0027) == CiA402State.OPERATION_ENABLED

    def test_infer_quick_stop_active(self):
        # b0=1, b1=1, b2=1, b5=0 (quick stop active)
        assert infer_cia402_state(0x0007) == CiA402State.QUICK_STOP_ACTIVE

    def test_infer_fault(self):
        assert infer_cia402_state(0x0008) == CiA402State.FAULT

    def test_infer_fault_reaction_active(self):
        assert infer_cia402_state(0x000F) == CiA402State.FAULT_REACTION_ACTIVE

    def test_infer_switch_on_disabled(self):
        assert infer_cia402_state(0x0040) == CiA402State.SWITCH_ON_DISABLED

    def test_infer_ready_to_switch_on(self):
        assert infer_cia402_state(0x0021) == CiA402State.READY_TO_SWITCH_ON

    def test_infer_switched_on(self):
        assert infer_cia402_state(0x0023) == CiA402State.SWITCHED_ON

    def test_infer_not_ready_to_switch_on(self):
        assert infer_cia402_state(0x0000) == CiA402State.NOT_READY_TO_SWITCH_ON

    def test_infer_with_extra_bits(self):
        # Operation enabled with remote (bit 9)
        assert infer_cia402_state(0x0227) == CiA402State.OPERATION_ENABLED
        # Operation enabled with target reached (bit 10)
        assert infer_cia402_state(0x0427) == CiA402State.OPERATION_ENABLED


class TestStateTransitions:
    """Tests for state transition patterns."""

    def test_state_sequence(self):
        states = [
            (0x0040, CiA402State.SWITCH_ON_DISABLED),
            (0x0021, CiA402State.READY_TO_SWITCH_ON),
            (0x0023, CiA402State.SWITCHED_ON),
            (0x0027, CiA402State.OPERATION_ENABLED),
        ]
        for sw, expected_state in states:
            state = infer_cia402_state(sw)
            assert state == expected_state, f"Statusword 0x{sw:04X} should be {expected_state}, got {state}"

    def test_fault_transitions(self):
        assert infer_cia402_state(0x0008) == CiA402State.FAULT
        assert infer_cia402_state(0x000F) == CiA402State.FAULT_REACTION_ACTIVE

    def test_quick_stop_transition(self):
        assert infer_cia402_state(0x0027) == CiA402State.OPERATION_ENABLED
        assert infer_cia402_state(0x0007) == CiA402State.QUICK_STOP_ACTIVE
