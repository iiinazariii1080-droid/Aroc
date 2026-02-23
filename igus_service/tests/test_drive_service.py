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
