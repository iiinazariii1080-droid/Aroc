"""Protocol layer for dryve D1 Modbus TCP Gateway.

Implements strict Modbus TCP Gateway telegram building/parsing according to the igus dryve D1 manual:
- Encapsulated Interface (function code 0x2B) with MEI type 0x0D
- Read telegrams MUST stop after Byte 18 (no data bytes in request)
- Length field (MBAP) must match the actual payload length
- Exception handling: 0x80 is added to the function code (Byte 7), and exception code is returned in Byte 8
"""

from .codec import (
    pack_int,
    pack_u16_le,
    unpack_int,
    unpack_u16_le,
)
from .exceptions import (
    ModbusExceptionCode,
    ModbusGatewayException,
    ProtocolError,
    ResponseMismatch,
    TelegramFormatError,
    TelegramValidationError,
)
from .gateway_telegram import (
    GatewayResponse,
    GatewayTelegram,
    build_read_adu,
    build_write_adu,
    parse_adu,
)
from .sdo import (
    SDOClient,
    SDOReadRequest,
    SDOWriteRequest,
)

__all__ = [
    # exceptions
    "ProtocolError",
    "TelegramFormatError",
    "TelegramValidationError",
    "ModbusExceptionCode",
    "ModbusGatewayException",
    "ResponseMismatch",
    # telegram
    "GatewayTelegram",
    "GatewayResponse",
    "build_read_adu",
    "build_write_adu",
    "parse_adu",
    # sdo
    "SDOClient",
    "SDOReadRequest",
    "SDOWriteRequest",
    # codec
    "pack_int",
    "unpack_int",
    "pack_u16_le",
    "unpack_u16_le",
]
