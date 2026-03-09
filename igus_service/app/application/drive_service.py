from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app import error_codes
from app.http_errors import error_detail, is_drive_connected

if TYPE_CHECKING:
    from app.events import EventBus
    from app.protocols import AppStateProtocol, DriveProtocol


@dataclass(frozen=True)
class ServiceError(Exception):
    status_code: int
    code: str
    message: str

    def to_error_detail(self) -> dict[str, Any]:
        return error_detail(self.code, self.message)


class DriveService:
    def __init__(self, app_state: AppStateProtocol) -> None:
        self._state = app_state

    def get_drive(self, *, require_connected: bool = False) -> DriveProtocol:
        drive: DriveProtocol | None = getattr(self._state, "drive", None)
        if drive is None:
            raise ServiceError(503, error_codes.DRIVE_NOT_INITIALIZED.code, error_codes.DRIVE_NOT_INITIALIZED.message)
        if require_connected and not is_drive_connected(drive):
            raise ServiceError(503, error_codes.DRIVE_OFFLINE.code, error_codes.DRIVE_OFFLINE.message)
        return drive

    def get_event_bus(self) -> EventBus:
        from app.events import EventBus as _EventBus

        event_bus: _EventBus | None = getattr(self._state, "event_bus", None)
        if event_bus is None:
            raise ServiceError(
                503,
                error_codes.EVENT_BUS_NOT_INITIALIZED.code,
                error_codes.EVENT_BUS_NOT_INITIALIZED.message,
            )
        return event_bus

    def require_motor_lock(self) -> asyncio.Lock:
        motor_lock: asyncio.Lock | None = getattr(self._state, "motor_lock", None)
        if motor_lock is None:
            raise ServiceError(
                503,
                error_codes.MOTOR_LOCK_NOT_INITIALIZED.code,
                error_codes.MOTOR_LOCK_NOT_INITIALIZED.message,
            )
        return motor_lock

    def get_motor_lock_optional(self) -> asyncio.Lock | None:
        return getattr(self._state, "motor_lock", None)

    async def require_not_in_fault(self) -> None:
        """Check that the drive is not in FAULT state.

        Reads the live statusword (bypasses telemetry cache) and raises
        ``ServiceError(503, SAFETY_LOCKOUT)`` if fault + DI7 low (safety relay open),
        or ``ServiceError(409, DRIVE_IN_FAULT)`` for a regular fault.
        Call this **before** acquiring ``motor_lock`` to fail fast.
        """
        drive = self.get_drive(require_connected=True)
        try:
            status = await drive.get_status_live()
        except Exception:
            # If we can't read status, let the downstream command handle it
            return
        if status.get("fault", False):
            if not status.get("remote", True):
                raise ServiceError(
                    503,
                    error_codes.SAFETY_LOCKOUT.code,
                    error_codes.SAFETY_LOCKOUT.message,
                )
            raise ServiceError(
                409,
                error_codes.DRIVE_IN_FAULT.code,
                error_codes.DRIVE_IN_FAULT.message,
            )

    async def read_fault_info(self) -> dict[str, Any]:
        """Read detailed fault diagnostics via FaultManager.

        Returns a dict with ``statusword``, ``error_code``, ``error_register``,
        ``history`` (all hex-formatted strings) or empty values if the drive is
        not in fault or diagnostics are unavailable.
        """
        drive = self.get_drive(require_connected=True)
        try:
            from drivers.dryve_d1.cia402.fault import FaultManager

            fm = FaultManager(drive)  # type: ignore[arg-type]  # runtime drive satisfies AsyncODAccessor
            info = await fm.read_fault_info(include_history=True)
            return info.as_dict()
        except Exception:
            return {"statusword": None, "error_code": None, "error_register": None, "history": None}

    @staticmethod
    def translate_driver_exception(op: str, exc: Exception) -> tuple[int, dict[str, Any]]:
        status_code = 500
        error_code = error_codes.INTERNAL_ERROR.code
        msg = f"{op} failed: {exc!s}"

        try:
            from drivers.dryve_d1.protocol.exceptions import (
                ModbusExceptionCode,
                ModbusGatewayException,
                ProtocolError,
            )
            if isinstance(exc, ModbusGatewayException):
                enum = exc.as_enum()
                status_code = 503
                error_code = f"MODBUS_{enum.name}" if enum is not None else error_codes.MODBUS_GATEWAY_ERROR.code
                if enum == ModbusExceptionCode.ILLEGAL_FUNCTION:
                    error_code = error_codes.MODBUS_ILLEGAL_FUNCTION.code
                    msg = (
                        f"{op} failed: {exc}. The remote Modbus server rejected function 0x2B "
                        f"(dryve D1 Modbus TCP Gateway). Check host/port and ensure the gateway is enabled; "
                        f"for the simulator in this project use port 501."
                    )
            elif isinstance(exc, ProtocolError):
                status_code = 503
                error_code = error_codes.PROTOCOL_ERROR.code
        except Exception:
            pass

        if isinstance(exc, asyncio.TimeoutError | TimeoutError):
            status_code = 504
            error_code = error_codes.TIMEOUT.code

        return status_code, error_detail(error_code, msg)
