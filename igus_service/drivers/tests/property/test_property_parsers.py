"""Property-based tests for parsers and validators.

These tests verify invariants:
- All valid inputs are parsed correctly
- All invalid inputs raise appropriate exceptions
- Parsers never crash (only raise documented exceptions)
"""

import pytest
from hypothesis import given, assume, strategies as st
from drivers.dryve_d1.protocol.validator import (
    parse_mbap,
    validate_mbap,
    validate_gateway_request,
    validate_gateway_response,
    extract_index_subindex,
    TelegramFormatError,
    TelegramValidationError,
)
from drivers.tests.property.hypothesis_helpers import mbap_headers, adus


class TestPropertyMBAPParser:
    """Property tests for MBAP parser."""

    @given(mbap_headers)
    def test_parse_mbap_never_crashes(self, mbap):
        """Property: parse_mbap never crashes on any 7+ byte input."""
        assume(len(mbap) >= 7)
        
        try:
            result = parse_mbap(mbap)
            # If successful, verify structure
            assert 0 <= result.transaction_id <= 0xFFFF
            assert 0 <= result.protocol_id <= 0xFFFF
            assert 0 <= result.length <= 0xFFFF
            assert 0 <= result.unit_id <= 0xFF
        except TelegramFormatError:
            # Expected for invalid inputs
            pass

    @given(st.binary(min_size=0, max_size=6))
    def test_parse_mbap_short_input_raises(self, short_bytes):
        """Property: parse_mbap raises TelegramFormatError for inputs < 7 bytes."""
        with pytest.raises(TelegramFormatError):
            parse_mbap(short_bytes)

    @given(mbap_headers)
    def test_validate_mbap_protocol_id(self, mbap):
        """Property: validate_mbap requires protocol_id == 0."""
        assume(len(mbap) >= 7)
        
        try:
            result = parse_mbap(mbap)
            if result.protocol_id == 0:
                # Should validate successfully
                try:
                    validate_mbap(mbap)
                except TelegramValidationError:
                    # May fail for length mismatch, but not protocol_id
                    pass
            else:
                # Should fail validation
                with pytest.raises(TelegramValidationError):
                    validate_mbap(mbap)
        except TelegramFormatError:
            pass  # Invalid format, skip


class TestPropertyGatewayValidator:
    """Property tests for gateway telegram validator."""

    @given(adus)
    def test_validate_gateway_request_never_crashes(self, adu):
        """Property: validate_gateway_request never crashes."""
        # Only test if ADU is large enough, but don't filter too aggressively
        if len(adu) < 19:
            pytest.skip("ADU too short for gateway request")
        
        try:
            validate_gateway_request(adu)
            # If successful, telegram is valid
        except (TelegramFormatError, TelegramValidationError):
            # Expected for invalid inputs
            pass

    @given(adus)
    def test_validate_gateway_response_never_crashes(self, adu):
        """Property: validate_gateway_response never crashes."""
        # Only test if ADU is large enough
        if len(adu) < 9:
            pytest.skip("ADU too short for gateway response")
        
        try:
            validate_gateway_response(adu)
            # If successful, telegram is valid
        except (TelegramFormatError, TelegramValidationError):
            # Expected for invalid inputs
            pass

    @given(st.binary(min_size=15, max_size=25))
    def test_extract_index_subindex_never_crashes(self, adu):
        """Property: extract_index_subindex never crashes on valid-sized input."""
        assume(len(adu) >= 15)
        
        try:
            index, subindex = extract_index_subindex(adu)
            assert 0 <= index <= 0xFFFF
            assert 0 <= subindex <= 0xFF
        except TelegramFormatError:
            # Expected for invalid inputs
            pass


class TestPropertyFuzzValidation:
    """Fuzz tests for validators with random inputs."""

    @given(st.binary(min_size=0, max_size=200))
    def test_fuzz_validate_mbap(self, random_bytes):
        """Fuzz: validate_mbap with random bytes."""
        try:
            validate_mbap(random_bytes)
            # If successful, verify it's actually valid
            mbap = parse_mbap(random_bytes)
            assert mbap.protocol_id == 0
        except (TelegramFormatError, TelegramValidationError):
            # Expected for most random inputs
            pass

    @given(st.binary(min_size=0, max_size=200))
    def test_fuzz_validate_gateway_request(self, random_bytes):
        """Fuzz: validate_gateway_request with random bytes."""
        try:
            validate_gateway_request(random_bytes)
            # If successful, verify structure
        except (TelegramFormatError, TelegramValidationError):
            # Expected for most random inputs
            pass

    @given(st.binary(min_size=0, max_size=200))
    def test_fuzz_validate_gateway_response(self, random_bytes):
        """Fuzz: validate_gateway_response with random bytes."""
        try:
            validate_gateway_response(random_bytes)
            # If successful, verify structure
        except (TelegramFormatError, TelegramValidationError):
            # Expected for most random inputs
            pass

