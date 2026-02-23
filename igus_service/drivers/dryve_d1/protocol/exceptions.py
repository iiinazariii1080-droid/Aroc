from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ProtocolError(Exception):
    """Base class for protocol-layer errors."""


class MotionAborted(Exception):
    """Raised when a motion command is interrupted by stop/quick_stop.

    This is NOT an error — it signals a deliberate user-initiated abort.
    Callers should treat this as a clean cancellation, not a fault.
    """


class TelegramFormatError(ProtocolError):
    """Telegram is syntactically malformed (too short, bad lengths, etc.)."""


class TelegramValidationError(ProtocolError):
    """Telegram violates dryve D1 gateway constraints (Byte 5/18 rules, reserved bytes, etc.)."""


class ResponseMismatch(ProtocolError):
    """Response does not match the request (transaction id, index, subindex, etc.)."""


class ModbusExceptionCode(IntEnum):
    """Modbus exception codes used by the dryve D1 gateway."""

    ILLEGAL_FUNCTION = 0x01
    ILLEGAL_DATA_ADDRESS = 0x02
    ILLEGAL_DATA_VALUE = 0x03
    DEVICE_FAILURE = 0x04
    ACKNOWLEDGE = 0x05
    SERVER_BUSY = 0x06

    @property
    def code_name(self) -> str:
        return {
            ModbusExceptionCode.ILLEGAL_FUNCTION: "Illegal Function Code",
            ModbusExceptionCode.ILLEGAL_DATA_ADDRESS: "Illegal Data Address",
            ModbusExceptionCode.ILLEGAL_DATA_VALUE: "Illegal Data Value",
            ModbusExceptionCode.DEVICE_FAILURE: "Device Failure",
            ModbusExceptionCode.ACKNOWLEDGE: "Acknowledge",
            ModbusExceptionCode.SERVER_BUSY: "Server Busy",
        }.get(self, f"Unknown({int(self):02X})")

    @property
    def description(self) -> str:
        return {
            ModbusExceptionCode.ILLEGAL_FUNCTION: "The device does not permit the used function code.",
            ModbusExceptionCode.ILLEGAL_DATA_ADDRESS: "The device does not permit the used data address/register address.",
            ModbusExceptionCode.ILLEGAL_DATA_VALUE: "The used data values are not allowable (may indicate an error in MBAP length).",
            ModbusExceptionCode.DEVICE_FAILURE: "An unrecoverable error occurred while the device attempted the requested action.",
            ModbusExceptionCode.ACKNOWLEDGE: "Request accepted; long processing time is required (prevents network timeout).",
            ModbusExceptionCode.SERVER_BUSY: "Device is busy; resend telegram when receiving device is available again.",
        }.get(self, "No description available.")


@dataclass(frozen=True, slots=True)
class ModbusGatewayException(ProtocolError):
    """Represents a Modbus TCP Gateway exception response.

    For dryve D1 Modbus TCP Gateway:
    - Function code in Byte 7 is 0x2B + 0x80 = 0xAB
    - Exception code is placed into Byte 8
    """

    function_code: int
    exception_code: int
    transaction_id: int | None = None
    unit_id: int | None = None

    def as_enum(self) -> ModbusExceptionCode | None:
        try:
            return ModbusExceptionCode(self.exception_code)
        except ValueError:
            return None

    def __str__(self) -> str:
        enum = self.as_enum()
        if enum is None:
            return (
                f"ModbusGatewayException(func=0x{self.function_code:02X}, "
                f"code=0x{self.exception_code:02X})"
            )
        return (
            f"ModbusGatewayException(func=0x{self.function_code:02X}, "
            f"code=0x{int(enum):02X} {enum.code_name})"
        )
