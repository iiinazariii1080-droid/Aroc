"""Property-based tests for statusword decoding and state inference.

These tests verify invariants:
- All 16-bit values decode successfully
- State inference never crashes
- Decoded flags are consistent
"""

import pytest
from hypothesis import given
from drivers.dryve_d1.od.statusword import (
    decode_statusword,
    infer_cia402_state,
    CiA402State,
)
from drivers.tests.property.hypothesis_helpers import statuswords


class TestPropertyStatuswordDecode:
    """Property tests for statusword decoding."""

    @given(statuswords)
    def test_decode_statusword_never_crashes(self, statusword):
        """Property: decode_statusword never crashes on any 16-bit value."""
        result = decode_statusword(statusword)
        
        # Verify structure and boolean values
        assert isinstance(result, dict)
        for key in ("ready_to_switch_on", "switched_on", "operation_enabled",
                     "fault", "quick_stop", "voltage_enabled", "remote"):
            assert isinstance(result[key], bool), f"{key} is not boolean: {result[key]}"

        # Verify bit correspondence for key flags
        assert result["ready_to_switch_on"] == bool(statusword & (1 << 0))
        assert result["operation_enabled"] == bool(statusword & (1 << 2))
        assert result["fault"] == bool(statusword & (1 << 3))
        assert result["remote"] == bool(statusword & (1 << 9))

    @given(statuswords)
    def test_infer_cia402_state_never_crashes(self, statusword):
        """Property: infer_cia402_state never crashes on any 16-bit value."""
        state = infer_cia402_state(statusword)
        
        # Verify it's a valid state
        assert isinstance(state, CiA402State)
        assert state in CiA402State

    @given(statuswords)
    def test_decode_inference_consistency(self, statusword):
        """Property: decoded flags are consistent with inferred state."""
        decoded = decode_statusword(statusword)
        state = infer_cia402_state(statusword)
        
        # Verify consistency for known states
        if state == CiA402State.OPERATION_ENABLED:
            assert decoded["operation_enabled"]
            assert not decoded["fault"]
        
        if state == CiA402State.FAULT:
            assert decoded["fault"]
        
        if state == CiA402State.QUICK_STOP_ACTIVE:
            assert not decoded["quick_stop"]

    @given(statuswords)
    def test_decode_bit_consistency(self, statusword):
        """Property: decoded bits match raw statusword bits."""
        decoded = decode_statusword(statusword)
        
        # Verify bit 0 (ready_to_switch_on)
        bit0 = bool((statusword >> 0) & 1)
        assert decoded["ready_to_switch_on"] == bit0
        
        # Verify bit 1 (switched_on)
        bit1 = bool((statusword >> 1) & 1)
        assert decoded["switched_on"] == bit1
        
        # Verify bit 2 (operation_enabled)
        bit2 = bool((statusword >> 2) & 1)
        assert decoded["operation_enabled"] == bit2
        
        # Verify bit 3 (fault)
        bit3 = bool((statusword >> 3) & 1)
        assert decoded["fault"] == bit3
        
        # Verify bit 5 (quick_stop)
        bit5 = bool((statusword >> 5) & 1)
        assert decoded["quick_stop"] == bit5

