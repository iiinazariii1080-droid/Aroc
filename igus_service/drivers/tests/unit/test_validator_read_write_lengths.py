import pytest

from drivers.dryve_d1.protocol.gateway_telegram import build_read_adu, build_write_adu
from drivers.dryve_d1.protocol.validator import validate_gateway_request, validate_gateway_response
from drivers.dryve_d1.protocol.exceptions import TelegramValidationError


def _make_read_response(transaction_id: int, unit_id: int, index: int, subindex: int, data: bytes) -> bytes:
    # Normal read response: total = 19 + len(data), MBAP length = 13 + len(data)
    bc = len(data)
    pdu = bytes([
        0x2B, 0x0D, 0x00, 0x00, 0x00,
        (index >> 8) & 0xFF, index & 0xFF, subindex & 0xFF,
        0x00, 0x00, 0x00,
        bc & 0xFF,
    ]) + data
    length = len(pdu) + 1  # unit_id included in length
    mbap = transaction_id.to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([unit_id])
    return mbap + pdu


def test_validator_rejects_read_request_with_data_bytes():
    telegram = build_read_adu(transaction_id=1, unit_id=1, index=0x6041, subindex=0, byte_count=2)
    good = telegram.adu
    validate_gateway_request(good, expect_write=False)

    bad = good + b"\x00"  # illegal extra byte after byte 18
    with pytest.raises(TelegramValidationError):
        validate_gateway_request(bad, expect_write=False)


def test_validator_accepts_read_response_exact_size():
    resp = _make_read_response(1, 1, 0x6041, 0, data=b"\x27\x00")
    validate_gateway_response(resp)


def test_validator_rejects_exception_response_wrong_size():
    # Exception response must be exactly 9 bytes per validator
    # Build MBAP length=3 and PDU=2 bytes but append extra to trigger error
    tid = 3
    unit = 1
    pdu = bytes([0x2B | 0x80, 0x01])
    length = len(pdu) + 1
    mbap = tid.to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([unit])
    bad = mbap + pdu + b"\x00"
    with pytest.raises(TelegramValidationError):
        validate_gateway_response(bad)
