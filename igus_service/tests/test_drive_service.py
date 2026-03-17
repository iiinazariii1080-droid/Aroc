from __future__ import annotations

import pytest

from app.application.drive_service import DriveService, ServiceError


class _State:
    pass


class _Drive:
    def __init__(self, connected: bool) -> None:
        self.is_connected = connected


def test_get_drive_not_initialized_raises() -> None:
    state = _State()
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        service.get_drive()

    assert exc.value.status_code == 503
    assert exc.value.code == "DRIVE_NOT_INITIALIZED"


def test_get_drive_require_connected_raises_offline() -> None:
    state = _State()
    state.drive = _Drive(connected=False)
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        service.get_drive(require_connected=True)

    assert exc.value.status_code == 503
    assert exc.value.code == "DRIVE_OFFLINE"


def test_require_motor_lock_missing_raises() -> None:
    state = _State()
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        service.require_motor_lock()

    assert exc.value.status_code == 503
    assert exc.value.code == "MOTOR_LOCK_NOT_INITIALIZED"


def test_translate_driver_exception_timeout_maps_504() -> None:
    code, detail = DriveService.translate_driver_exception("status", TimeoutError())

    assert code == 504
    assert detail["code"] == "TIMEOUT"


# ---------------------------------------------------------------------------
# require_not_in_fault — fail-closed fault gate
# ---------------------------------------------------------------------------

class _DriveWithStatus:
    """Drive stub that returns a configurable status from get_status_live."""

    def __init__(self, *, connected: bool = True, status: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.is_connected = connected
        self._status = status or {}
        self._raise_exc = raise_exc

    async def get_status_live(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._status


async def test_require_not_in_fault_raises_drive_in_fault() -> None:
    """Active fault → ServiceError(409, DRIVE_IN_FAULT)."""
    state = _State()
    state.drive = _DriveWithStatus(status={"fault": True, "remote": True, "operation_enabled": False})
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        await service.require_not_in_fault()

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_require_not_in_fault_raises_safety_lockout() -> None:
    """Fault + remote=False → ServiceError(503, SAFETY_LOCKOUT)."""
    state = _State()
    state.drive = _DriveWithStatus(status={"fault": True, "remote": False, "operation_enabled": False})
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        await service.require_not_in_fault()

    assert exc.value.status_code == 503
    assert exc.value.code == "SAFETY_LOCKOUT"


async def test_require_not_in_fault_raises_fault_check_failed_on_read_error() -> None:
    """If get_status_live raises, motion must be blocked (fail-closed)."""
    state = _State()
    state.drive = _DriveWithStatus(raise_exc=ConnectionError("Modbus lost"))
    service = DriveService(state)

    with pytest.raises(ServiceError) as exc:
        await service.require_not_in_fault()

    assert exc.value.status_code == 503
    assert exc.value.code == "FAULT_CHECK_FAILED"


async def test_require_not_in_fault_passes_when_no_fault() -> None:
    """No fault → returns without raising."""
    state = _State()
    state.drive = _DriveWithStatus(status={"fault": False, "remote": True, "operation_enabled": True})
    service = DriveService(state)

    await service.require_not_in_fault()  # must not raise
