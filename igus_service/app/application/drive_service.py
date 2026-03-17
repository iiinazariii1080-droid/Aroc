from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app import error_codes
from app.application.results import FaultDetailsResult
from app.http_errors import error_detail

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.events import EventBus
    from app.protocols import AppStateProtocol, DriveProtocol


class ServiceError(Exception):
    """Application-layer error with HTTP status code and error code.

    Not a frozen dataclass — Exception subclasses must allow __traceback__
    assignment (Python 3.13+ contextlib sets it during exception handling).
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def to_error_detail(self) -> dict[str, Any]:
        return error_detail(self.code, self.message)


def is_drive_connected(drive: Any) -> bool:
    """Check whether the drive reports itself as connected."""
    return bool(getattr(drive, "is_connected", False))


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

    async def require_not_in_fault(self) -> None:
        """Check that the drive is not in FAULT state — fail-closed.

        Reads the live statusword (bypasses telemetry cache) and raises:
        - ``ServiceError(503, SAFETY_LOCKOUT)``  if fault + DI7 low (safety relay open)
        - ``ServiceError(409, DRIVE_IN_FAULT)``  for a regular active fault
        - ``ServiceError(503, FAULT_CHECK_FAILED)`` if the status cannot be read at all

        The last case is intentionally fail-closed: when the drive state is unknown
        (Modbus error, timeout, disconnected), blocking the motion command is the
        safe choice.  Passing through silently could allow movement in FAULT state.

        Call this **before** acquiring ``motor_lock`` to fail fast without queuing.
        """
        drive = self.get_drive(require_connected=True)
        try:
            status = await drive.get_status_live()
        except Exception as exc:
            _LOGGER.warning(
                "require_not_in_fault: could not read live status (%s: %s) — blocking motion (fail-closed)",
                type(exc).__name__, exc,
            )
            raise ServiceError(
                503,
                error_codes.FAULT_CHECK_FAILED.code,
                f"{error_codes.FAULT_CHECK_FAILED.message}: {exc!s}",
            ) from exc
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

    async def read_fault_info(self) -> FaultDetailsResult | None:
        """Read detailed fault diagnostics via FaultManager.

        Returns a ``FaultDetailsResult`` on success, or ``None`` if the read fails
        (drive unreachable, Modbus error, etc.).  Callers should treat ``None`` as
        "diagnostics unavailable" rather than "no fault".
        """
        drive = self.get_drive(require_connected=True)
        try:
            raw = await drive.read_fault_info()
            ec = raw.get("error_code")
            er = raw.get("error_register")
            hist = raw.get("history")
            return FaultDetailsResult(
                error_code=str(ec) if ec is not None else None,
                error_register=str(er) if er is not None else None,
                history=[str(h) for h in hist] if hist is not None else None,
            )
        except Exception:
            _LOGGER.warning("Failed to read fault diagnostics from drive", exc_info=True)
            return None

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
        except ImportError:
            # Driver package not installed — skip Modbus-specific classification.
            pass
        else:
            if isinstance(exc, ModbusGatewayException):
                enum = exc.as_enum()
                status_code = 503
                # Use registered error codes only — no dynamic MODBUS_{name} generation.
                if enum == ModbusExceptionCode.ILLEGAL_FUNCTION:
                    error_code = error_codes.MODBUS_ILLEGAL_FUNCTION.code
                    msg = (
                        f"{op} failed: {exc}. The remote Modbus server rejected function 0x2B "
                        f"(dryve D1 Modbus TCP Gateway). Check host/port and ensure the gateway is enabled; "
                        f"for the simulator in this project use port 501."
                    )
                else:
                    error_code = error_codes.MODBUS_GATEWAY_ERROR.code
                    enum_label = enum.name if enum is not None else "UNKNOWN"
                    msg = f"{op} failed: Modbus exception {enum_label} — {exc!s}"
            elif isinstance(exc, ProtocolError):
                status_code = 503
                error_code = error_codes.PROTOCOL_ERROR.code

        if isinstance(exc, asyncio.TimeoutError | TimeoutError):
            status_code = 504
            error_code = error_codes.TIMEOUT.code

        return status_code, error_detail(error_code, msg)
