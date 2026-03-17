from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorCode:
    code: str
    message: str


DRIVE_NOT_INITIALIZED = ErrorCode("DRIVE_NOT_INITIALIZED", "Driver not initialized")
DRIVE_OFFLINE = ErrorCode("DRIVE_OFFLINE", "Driver is not connected")
EVENT_BUS_NOT_INITIALIZED = ErrorCode("EVENT_BUS_NOT_INITIALIZED", "Event bus not initialized")
MOTOR_LOCK_NOT_INITIALIZED = ErrorCode("MOTOR_LOCK_NOT_INITIALIZED", "Motor lock not initialized")
STATUS_READ_FAILED = ErrorCode("STATUS_READ_FAILED", "Failed to read drive status")
TELEMETRY_READ_FAILED = ErrorCode("TELEMETRY_READ_FAILED", "Failed to read telemetry")
LEGACY_API_REMOVED = ErrorCode("LEGACY_API_REMOVED", "Legacy API endpoint is removed")
VALIDATION_ERROR = ErrorCode("VALIDATION_ERROR", "Request validation failed")
INTERNAL_ERROR = ErrorCode("INTERNAL_ERROR", "Internal server error")
REQUEST_ERROR = ErrorCode("REQUEST_ERROR", "Request failed")
TIMEOUT = ErrorCode("TIMEOUT", "Request timed out")
PROTOCOL_ERROR = ErrorCode("PROTOCOL_ERROR", "Protocol error")
MODBUS_GATEWAY_ERROR = ErrorCode("MODBUS_GATEWAY_ERROR", "Modbus gateway error")
MODBUS_ILLEGAL_FUNCTION = ErrorCode("MODBUS_ILLEGAL_FUNCTION", "Illegal function for remote Modbus gateway")
DRIVE_IN_FAULT = ErrorCode("DRIVE_IN_FAULT", "Drive is in FAULT state — call fault_reset first")
SAFETY_LOCKOUT = ErrorCode("SAFETY_LOCKOUT", "Safety lockout: DI7 (Enable) is LOW — check E-Stop and safety relay")
DRIVE_DEGRADED = ErrorCode("DRIVE_DEGRADED", "Drive health score is below the readiness threshold")
MOTOR_BUSY = ErrorCode("MOTOR_BUSY", "Motor is busy with another operation — try again after the current command completes")
FAULT_CHECK_FAILED = ErrorCode("FAULT_CHECK_FAILED", "Could not read drive status before motion — command blocked (fail-closed)")


__all__ = [
    "DRIVE_IN_FAULT",
    "DRIVE_NOT_INITIALIZED",
    "DRIVE_DEGRADED",
    "DRIVE_OFFLINE",
    "EVENT_BUS_NOT_INITIALIZED",
    "FAULT_CHECK_FAILED",
    "INTERNAL_ERROR",
    "LEGACY_API_REMOVED",
    "MODBUS_GATEWAY_ERROR",
    "MODBUS_ILLEGAL_FUNCTION",
    "MOTOR_BUSY",
    "MOTOR_LOCK_NOT_INITIALIZED",
    "PROTOCOL_ERROR",
    "REQUEST_ERROR",
    "SAFETY_LOCKOUT",
    "STATUS_READ_FAILED",
    "TELEMETRY_READ_FAILED",
    "TIMEOUT",
    "VALIDATION_ERROR",
    "ErrorCode",
]
