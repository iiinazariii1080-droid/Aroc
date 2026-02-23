"""Unit tests for controlword helpers."""

import pytest

from drivers.dryve_d1.od.controlword import (
    CWBit,
    cw_set_bits,
    cw_clear_bits,
    cw_with_bit,
    cw_disable_voltage,
    cw_shutdown,
    cw_switch_on,
    cw_enable_operation,
    cw_quick_stop,
    cw_quick_stop_canonical,
    cw_fault_reset,
    cw_pulse_new_set_point,
)


class TestCWBit:
    """Tests for CWBit enum."""

    def test_cwbit_values(self):
        assert CWBit.SWITCH_ON == 0
        assert CWBit.ENABLE_VOLTAGE == 1
        assert CWBit.QUICK_STOP == 2
        assert CWBit.ENABLE_OPERATION == 3
        assert CWBit.NEW_SET_POINT == 4
        assert CWBit.CHANGE_SET_IMMEDIATELY == 5
        assert CWBit.ABS_REL == 6
        assert CWBit.FAULT_RESET == 7
        assert CWBit.HALT == 8


class TestBitManipulation:
    """Tests for bit manipulation functions."""

    def test_cw_set_bits(self):
        word = 0x0000
        result = cw_set_bits(word, CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE)
        assert result == 0x0003
        result2 = cw_set_bits(result, CWBit.SWITCH_ON)
        assert result2 == result

    def test_cw_clear_bits(self):
        word = 0x000F
        result = cw_clear_bits(word, CWBit.QUICK_STOP)
        assert result == 0x000B
        result2 = cw_clear_bits(result, CWBit.QUICK_STOP)
        assert result2 == result

    def test_cw_with_bit(self):
        word = 0x0000
        result = cw_with_bit(word, CWBit.SWITCH_ON, True)
        assert result == 0x0001
        result2 = cw_with_bit(result, CWBit.SWITCH_ON, False)
        assert result2 == 0x0000
        word3 = 0x0000
        result3 = cw_with_bit(word3, CWBit.SWITCH_ON, True)
        result3 = cw_with_bit(result3, CWBit.ENABLE_VOLTAGE, True)
        assert result3 == 0x0003


class TestCanonicalCommandWords:
    """Tests for canonical CiA402 command words."""

    def test_cw_disable_voltage(self):
        assert cw_disable_voltage() == 0x0000

    def test_cw_shutdown(self):
        assert cw_shutdown() == 0x0006
        assert (cw_shutdown() & 0x0006) == 0x0006

    def test_cw_switch_on(self):
        assert cw_switch_on() == 0x0007
        assert (cw_switch_on() & 0x0007) == 0x0007

    def test_cw_enable_operation(self):
        assert cw_enable_operation() == 0x000F
        assert (cw_enable_operation() & 0x000F) == 0x000F

    def test_cw_quick_stop(self):
        assert cw_quick_stop() == 0x0002
        assert (cw_quick_stop() & 0x0002) == 0x0002

    def test_cw_quick_stop_canonical(self):
        base = 0x000F
        result = cw_quick_stop_canonical(base)
        assert result == 0x000B
        result2 = cw_quick_stop_canonical()
        assert result2 == 0x000B

    def test_cw_fault_reset(self):
        assert cw_fault_reset() == 0x0080
        assert (cw_fault_reset() & 0x0080) == 0x0080


class TestProfileModeHelpers:
    """Tests for profile mode helper functions."""

    def test_cw_pulse_new_set_point(self):
        base = 0x000F
        set_word, clear_word = cw_pulse_new_set_point(base)
        assert (set_word & (1 << 4)) != 0
        assert set_word == 0x001F
        assert (clear_word & (1 << 4)) == 0
        assert clear_word == base
        assert set_word != clear_word
        assert (set_word & ~(1 << 4)) == clear_word

    def test_cw_pulse_new_set_point_with_different_base(self):
        base = 0x0000
        set_word, clear_word = cw_pulse_new_set_point(base)
        assert (set_word & (1 << 4)) != 0
        assert (clear_word & (1 << 4)) == 0
        assert clear_word == base


class TestCombinedOperations:
    """Tests for combined bit operations."""

    def test_set_multiple_bits(self):
        word = 0x0000
        result = cw_set_bits(
            word, CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION,
        )
        assert result == 0x000F

    def test_clear_multiple_bits(self):
        word = 0x000F
        result = cw_clear_bits(word, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION)
        assert result == 0x0003

    def test_set_and_clear_sequence(self):
        word = 0x0000
        word = cw_set_bits(word, CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION)
        assert word == 0x000F
        word = cw_clear_bits(word, CWBit.QUICK_STOP)
        assert word == 0x000B
        word = cw_set_bits(word, CWBit.HALT)
        assert word == 0x010B

    def test_hold_bits_preservation(self):
        base = cw_enable_operation()
        with_halt = cw_set_bits(base, CWBit.HALT)
        assert (with_halt & 0x000F) == 0x000F
        assert (with_halt & 0x0100) != 0
        without_quick_stop = cw_clear_bits(base, CWBit.QUICK_STOP)
        assert (without_quick_stop & 0x000B) == 0x000B
        assert (without_quick_stop & 0x0004) == 0
