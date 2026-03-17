"""Property-based tests for codec (pack/unpack).

These tests verify invariants:
- Round-trip: pack(value) → unpack → value
- All values in range pack successfully
- Out-of-range values raise ValueError
"""

import pytest
from hypothesis import given, assume, strategies as st
from drivers.dryve_d1.protocol.codec import (
    pack_int,
    unpack_int,
    pack_u16_le,
    unpack_u16_le,
    pack_i32_le,
    unpack_i32_le,
)
from drivers.tests.property.hypothesis_helpers import signed_i32, unsigned_u16, byte_sizes


class TestPropertyPackUnpack:
    """Property tests for pack/unpack round-trip."""

    @given(signed_i32, byte_sizes)
    def test_pack_unpack_signed_roundtrip(self, value, size):
        """Property: pack → unpack → original value for signed integers."""
        assume(size in (1, 2, 3, 4))
        
        # Calculate valid range for this size
        bits = size * 8
        min_val = -(2 ** (bits - 1))
        max_val = (2 ** (bits - 1)) - 1
        
        if min_val <= value <= max_val:
            packed = pack_int(value, size=size, signed=True, endian="<")
            unpacked = unpack_int(packed, signed=True, endian="<")
            assert unpacked == value, f"Round-trip failed: {value} → {unpacked}"

    @given(unsigned_u16)
    def test_pack_unpack_u16_roundtrip(self, value):
        """Property: pack_u16_le → unpack_u16_le → original value."""
        packed = pack_u16_le(value)
        unpacked = unpack_u16_le(packed)
        assert unpacked == value, f"Round-trip failed: {value} → {unpacked}"

    @given(signed_i32)
    def test_pack_unpack_i32_roundtrip(self, value):
        """Property: pack_i32_le → unpack_i32_le → original value."""
        packed = pack_i32_le(value)
        unpacked = unpack_i32_le(packed)
        assert unpacked == value, f"Round-trip failed: {value} → {unpacked}"

    @given(signed_i32, byte_sizes)
    def test_pack_out_of_range_raises(self, value, size):
        """Property: pack raises ValueError for out-of-range values."""
        assume(size in (1, 2, 3, 4))
        
        bits = size * 8
        min_val = -(2 ** (bits - 1))
        max_val = (2 ** (bits - 1)) - 1
        
        if value < min_val or value > max_val:
            with pytest.raises(ValueError):
                pack_int(value, size=size, signed=True, endian="<")

    @given(st.binary(min_size=1, max_size=4))
    def test_unpack_never_crashes(self, data):
        """Property: unpack never crashes on valid-sized input."""
        try:
            result = unpack_int(data, signed=True, endian="<")
            # If successful, result should be in valid range
            assert isinstance(result, int)
        except ValueError:
            # Expected for invalid sizes
            pass


class TestPropertyCodecBoundaries:
    """Property tests for boundary values."""

    @given(byte_sizes)
    def test_pack_min_max_signed(self, size):
        """Property: pack handles min/max signed values correctly."""
        assume(size in (1, 2, 3, 4))
        
        bits = size * 8
        min_val = -(2 ** (bits - 1))
        max_val = (2 ** (bits - 1)) - 1
        
        # Test min value
        packed_min = pack_int(min_val, size=size, signed=True, endian="<")
        unpacked_min = unpack_int(packed_min, signed=True, endian="<")
        assert unpacked_min == min_val
        
        # Test max value
        packed_max = pack_int(max_val, size=size, signed=True, endian="<")
        unpacked_max = unpack_int(packed_max, signed=True, endian="<")
        assert unpacked_max == max_val

    @given(byte_sizes)
    def test_pack_min_max_unsigned(self, size):
        """Property: pack handles min/max unsigned values correctly."""
        assume(size in (1, 2, 3, 4))
        
        bits = size * 8
        min_val = 0
        max_val = (2 ** bits) - 1
        
        # Test min value
        packed_min = pack_int(min_val, size=size, signed=False, endian="<")
        unpacked_min = unpack_int(packed_min, signed=False, endian="<")
        assert unpacked_min == min_val
        
        # Test max value
        packed_max = pack_int(max_val, size=size, signed=False, endian="<")
        unpacked_max = unpack_int(packed_max, signed=False, endian="<")
        assert unpacked_max == max_val

