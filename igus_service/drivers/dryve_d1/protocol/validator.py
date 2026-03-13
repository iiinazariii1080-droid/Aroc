from __future__ import annotations

import logging
from dataclasses import dataclass

from .exceptions import TelegramFormatError, TelegramValidationError

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MBAP:
    transaction_id: int
    protocol_id: int
    length: int
    unit_id: int


GATEWAY_FUNCTION_CODE = 0x2B
GATEWAY_EXCEPTION_MASK = 0x80
GATEWAY_MEI_TYPE = 0x0D  # per manual

# PDU layout (normal, non-exception):
# Byte 7  : Function code (0x2B)
# Byte 8  : MEI type (0x0D)
# Byte 9  : Protocol control (0=read, 1=write)
# Byte 10 : Reserved (0)
# Byte 11 : Node ID (0)
# Byte 12 : Object index high byte (MSB)
# Byte 13 : Object index low byte (LSB)
# Byte 14 : Subindex
# Byte 15..17 : Starting address / reserved (0)
# Byte 18 : Byte count (1..4 for read/write, 0 for some write acks)
# Byte 19..22 : Data field (little-endian), present only for write requests and read responses


def parse_mbap(adu: bytes) -> MBAP:
    if len(adu) < 7:
        raise TelegramFormatError(f"ADU too short for MBAP header: {len(adu)} bytes")
    # Big endian: Transaction ID, Protocol ID, Length, Unit ID
    tid = (adu[0] << 8) | adu[1]
    proto = (adu[2] << 8) | adu[3]
    length = (adu[4] << 8) | adu[5]
    unit_id = adu[6]
    return MBAP(transaction_id=tid, protocol_id=proto, length=length, unit_id=unit_id)


def validate_mbap(adu: bytes) -> MBAP:
    mbap = parse_mbap(adu)
    if mbap.protocol_id != 0:
        raise TelegramValidationError(f"Protocol ID must be 0, got {mbap.protocol_id}")
    # MBAP.length counts bytes after byte 5: unit_id + PDU
    expected_total = 6 + mbap.length
    if len(adu) != expected_total:
        raise TelegramValidationError(
            f"Length mismatch: MBAP length={mbap.length} implies total={expected_total}, actual={len(adu)}"
        )
    return mbap


def _require_byte(adu: bytes, idx: int, expected: int, *, name: str) -> None:
    if adu[idx] != expected:
        raise TelegramValidationError(f"{name} mismatch at byte {idx}: expected 0x{expected:02X}, got 0x{adu[idx]:02X}")


def validate_gateway_request(adu: bytes, *, expect_write: bool | None = None) -> None:
    """Validate an outgoing request telegram according to the manual constraints."""
    mbap = validate_mbap(adu)

    if len(adu) < 19:
        raise TelegramValidationError("Gateway request must be at least 19 bytes (read request)")

    # Byte 7 is function code
    _require_byte(adu, 7, GATEWAY_FUNCTION_CODE, name="Function code")
    _require_byte(adu, 8, GATEWAY_MEI_TYPE, name="MEI type")

    proto_ctrl = adu[9]
    if proto_ctrl not in (0, 1):
        raise TelegramValidationError(f"Protocol control (byte 9) must be 0/1, got {proto_ctrl}")

    if expect_write is not None and proto_ctrl != (1 if expect_write else 0):
        raise TelegramValidationError(f"Unexpected protocol control: got {proto_ctrl}, expect {'write' if expect_write else 'read'}")

    # Reserved bytes must be zero (per manual)
    _require_byte(adu, 10, 0x00, name="Reserved byte 10")
    _require_byte(adu, 11, 0x00, name="Node ID byte 11")
    _require_byte(adu, 15, 0x00, name="Starting address byte 15")
    _require_byte(adu, 16, 0x00, name="Starting address byte 16")
    _require_byte(adu, 17, 0x00, name="Reserved byte 17")

    byte_count = adu[18]
    if proto_ctrl == 0:
        # READ: byte5 must be 0x0D and telegram must stop after byte 18 (no data bytes)
        if byte_count not in (1, 2, 3, 4):
            raise TelegramValidationError(f"Read byte_count must be 1..4, got {byte_count}")
        if mbap.length != 13:
            raise TelegramValidationError(f"Read request MBAP length must be 13 (0x0D), got {mbap.length}")
        if len(adu) != 19:
            raise TelegramValidationError(f"Read request must be exactly 19 bytes, got {len(adu)}")
    else:
        # WRITE: data bytes must exist and match byte_count; mbap.length must be 13+byte_count
        if byte_count not in (1, 2, 3, 4):
            raise TelegramValidationError(f"Write byte_count must be 1..4, got {byte_count}")
        expected_len = 13 + byte_count
        if mbap.length != expected_len:
            raise TelegramValidationError(f"Write request MBAP length must be {expected_len}, got {mbap.length}")
        expected_total = 19 + byte_count
        if len(adu) != expected_total:
            raise TelegramValidationError(f"Write request total size must be {expected_total}, got {len(adu)}")


def validate_gateway_response(adu: bytes) -> None:
    """Validate a received response telegram (basic structural checks)."""
    validate_mbap(adu)
    if len(adu) < 9:
        raise TelegramValidationError("Gateway response must be at least 9 bytes")

    func = adu[7]
    if func == (GATEWAY_FUNCTION_CODE | GATEWAY_EXCEPTION_MASK):
        # Exception response: function code + exception code (bytes 7-8).
        # The manual specifies 9 bytes, but some dryve firmware versions
        # include additional gateway fields (MEI type, index, etc.) making
        # the frame longer (e.g. 17 bytes).  Accept any length >= 9 so the
        # exception code can be parsed and reported to the caller.
        if len(adu) > 9:
            _log.debug(
                "Exception response is %d bytes (expected 9); "
                "extra bytes tolerated: %s",
                len(adu),
                adu[9:].hex(),
            )
        return

    # Normal response: must contain MEI type and at least through byte_count
    if func != GATEWAY_FUNCTION_CODE:
        raise TelegramValidationError(f"Unexpected function code in response: 0x{func:02X}")
    _require_byte(adu, 8, GATEWAY_MEI_TYPE, name="MEI type")

    if len(adu) < 19:
        raise TelegramValidationError("Normal gateway response must be at least 19 bytes")

    proto_ctrl = adu[9]
    if proto_ctrl not in (0, 1):
        raise TelegramValidationError(f"Protocol control (byte 9) must be 0/1, got {proto_ctrl}")

    # Reserved bytes should be zero (device should follow this)
    _require_byte(adu, 10, 0x00, name="Reserved byte 10")
    _require_byte(adu, 11, 0x00, name="Node ID byte 11")
    _require_byte(adu, 15, 0x00, name="Starting address byte 15")
    _require_byte(adu, 16, 0x00, name="Starting address byte 16")
    _require_byte(adu, 17, 0x00, name="Reserved byte 17")

    byte_count = adu[18]
    if proto_ctrl == 0:
        # Read response SHOULD include byte_count data bytes
        if byte_count not in (1, 2, 3, 4):
            raise TelegramValidationError(f"Read response byte_count must be 1..4, got {byte_count}")
        expected_total = 19 + byte_count
        if len(adu) != expected_total:
            raise TelegramValidationError(f"Read response total size must be {expected_total}, got {len(adu)}")
    else:
        # Write response is typically a handshake without data; byte_count may be 0.
        # Accept both 0 (no data) and 1..4 (echoed data) to be tolerant.
        if byte_count == 0:
            if len(adu) != 19:
                raise TelegramValidationError(f"Write ack response with byte_count=0 must be 19 bytes, got {len(adu)}")
        elif byte_count in (1, 2, 3, 4):
            expected_total = 19 + byte_count
            if len(adu) != expected_total:
                raise TelegramValidationError(f"Write response total size must be {expected_total}, got {len(adu)}")
        else:
            raise TelegramValidationError(f"Write response byte_count must be 0..4, got {byte_count}")


def extract_index_subindex(adu: bytes) -> tuple[int, int]:
    """Return (index, subindex) from a normal gateway telegram."""
    if len(adu) < 15:
        raise TelegramFormatError("ADU too short to extract index/subindex")
    index = (adu[12] << 8) | adu[13]
    sub = adu[14]
    return index, sub
