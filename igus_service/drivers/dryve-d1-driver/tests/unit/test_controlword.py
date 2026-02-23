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
        """Test that CWBit enum has correct values."""
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
        """Test setting bits in controlword."""
        word = 0x0000
        result = cw_set_bits(word, CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE)
        assert result == 0x0003  # bits 0 and 1 set
        
        # Setting already set bits should not change result
        result2 = cw_set_bits(result, CWBit.SWITCH_ON)
        assert result2 == result
    
    def test_cw_clear_bits(self):
        """Test clearing bits in controlword."""
        word = 0x000F  # bits 0-3 set
        result = cw_clear_bits(word, CWBit.QUICK_STOP)
        assert result == 0x000B  # bit 2 cleared
        
        # Clearing already cleared bits should not change result
        result2 = cw_clear_bits(result, CWBit.QUICK_STOP)
        assert result2 == result
    
    def test_cw_with_bit(self):
        """Test setting/clearing a single bit."""
        word = 0x0000
        
        # Set bit
        result = cw_with_bit(word, CWBit.SWITCH_ON, True)
        assert result == 0x0001
        
        # Clear bit
        result2 = cw_with_bit(result, CWBit.SWITCH_ON, False)
        assert result2 == 0x0000
        
        # Set multiple bits
        word3 = 0x0000
        result3 = cw_with_bit(word3, CWBit.SWITCH_ON, True)
        result3 = cw_with_bit(result3, CWBit.ENABLE_VOLTAGE, True)
        assert result3 == 0x0003


class TestCanonicalCommandWords:
    """Tests for canonical CiA402 command words."""
    
    def test_cw_disable_voltage(self):
        """Test disable voltage command."""
        assert cw_disable_voltage() == 0x0000
    
    def test_cw_shutdown(self):
        """Test shutdown command."""
        assert cw_shutdown() == 0x0006
        # Should have bits 1 (enable voltage) and 2 (quick stop)
        assert (cw_shutdown() & 0x0006) == 0x0006
    
    def test_cw_switch_on(self):
        """Test switch on command."""
        assert cw_switch_on() == 0x0007
        # Should have bits 0, 1, 2
        assert (cw_switch_on() & 0x0007) == 0x0007
    
    def test_cw_enable_operation(self):
        """Test enable operation command."""
        assert cw_enable_operation() == 0x000F
        # Should have bits 0-3
        assert (cw_enable_operation() & 0x000F) == 0x000F
    
    def test_cw_quick_stop(self):
        """Test quick stop command (legacy)."""
        assert cw_quick_stop() == 0x0002
        # Should have bit 1 (enable voltage)
        assert (cw_quick_stop() & 0x0002) == 0x0002
    
    def test_cw_quick_stop_canonical(self):
        """Test canonical quick stop command."""
        base = 0x000F  # Operation enabled
        result = cw_quick_stop_canonical(base)
        # Should clear bit 2 (QUICK_STOP) while keeping bits 0-3
        assert result == 0x000B  # 0x000F with bit 2 cleared
        
        # Test with default base
        result2 = cw_quick_stop_canonical()
        assert result2 == 0x000B
    
    def test_cw_fault_reset(self):
        """Test fault reset command."""
        assert cw_fault_reset() == 0x0080
        # Should have bit 7
        assert (cw_fault_reset() & 0x0080) == 0x0080


class TestProfileModeHelpers:
    """Tests for profile mode helper functions."""
    
    def test_cw_pulse_new_set_point(self):
        """Test NEW_SET_POINT pulse generation."""
        base = 0x000F  # Operation enabled
        set_word, clear_word = cw_pulse_new_set_point(base)
        
        # Set word should have NEW_SET_POINT bit (bit 4)
        assert (set_word & (1 << 4)) != 0
        assert set_word == 0x001F  # base + bit 4
        
        # Clear word should not have NEW_SET_POINT bit
        assert (clear_word & (1 << 4)) == 0
        assert clear_word == base
        
        # Verify they can be used to pulse
        assert set_word != clear_word
        assert (set_word & ~(1 << 4)) == clear_word
    
    def test_cw_pulse_new_set_point_with_different_base(self):
        """Test NEW_SET_POINT pulse with different base values."""
        base = 0x0000
        set_word, clear_word = cw_pulse_new_set_point(base)
        
        assert (set_word & (1 << 4)) != 0
        assert (clear_word & (1 << 4)) == 0
        assert clear_word == base


class TestCombinedOperations:
    """Tests for combined bit operations."""
    
    def test_set_multiple_bits(self):
        """Test setting multiple bits at once."""
        word = 0x0000
        result = cw_set_bits(
            word,
            CWBit.SWITCH_ON,
            CWBit.ENABLE_VOLTAGE,
            CWBit.QUICK_STOP,
            CWBit.ENABLE_OPERATION,
        )
        assert result == 0x000F  # bits 0-3 set
    
    def test_clear_multiple_bits(self):
        """Test clearing multiple bits at once."""
        word = 0x000F  # bits 0-3 set
        result = cw_clear_bits(
            word,
            CWBit.QUICK_STOP,
            CWBit.ENABLE_OPERATION,
        )
        assert result == 0x0003  # only bits 0-1 remain
    
    def test_set_and_clear_sequence(self):
        """Test setting and clearing bits in sequence."""
        word = 0x0000
        
        # Set bits 0-3
        word = cw_set_bits(word, CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION)
        assert word == 0x000F
        
        # Clear bit 2 (QUICK_STOP)
        word = cw_clear_bits(word, CWBit.QUICK_STOP)
        assert word == 0x000B
        
        # Set bit 8 (HALT)
        word = cw_set_bits(word, CWBit.HALT)
        assert word == 0x010B
    
    def test_hold_bits_preservation(self):
        """Test that hold bits (0-3) can be preserved while modifying other bits."""
        base = cw_enable_operation()  # 0x000F (bits 0-3)
        
        # Add HALT bit while preserving hold bits
        with_halt = cw_set_bits(base, CWBit.HALT)
        assert (with_halt & 0x000F) == 0x000F  # Hold bits preserved
        assert (with_halt & 0x0100) != 0  # HALT bit set
        
        # Clear QUICK_STOP while preserving other hold bits
        without_quick_stop = cw_clear_bits(base, CWBit.QUICK_STOP)
        assert (without_quick_stop & 0x000B) == 0x000B  # Bits 0,1,3 preserved
        assert (without_quick_stop & 0x0004) == 0  # Bit 2 cleared

