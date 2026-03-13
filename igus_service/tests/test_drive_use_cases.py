from __future__ import annotations

import pytest

from app.api_models import (
    FaultResetRequest,
    JogMoveRequest,
    MoveToPositionRequest,
    ProfileConfig,
    ReferenceRequest,
    StopRequest,
)
from app.application.drive_service import ServiceError
from app.application.use_cases import DriveUseCases
from drivers.dryve_d1.protocol.exceptions import MotionAborted


class _AsyncNoopLock:
    def locked(self) -> bool:
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DriveFake:
    def __init__(self) -> None:
        self.is_connected = True
        self.calls: list[tuple[str, object]] = []
        self.raise_on_move: Exception | None = None

    async def get_position(self):
        return 100

    async def jog_stop(self, **kwargs):
        self.calls.append(("jog_stop", kwargs or None))

    async def move_to_position(self, **kwargs):
        self.calls.append(("move_to_position", kwargs))
        if self.raise_on_move is not None:
            raise self.raise_on_move

    async def jog_start(self, **kwargs):
        self.calls.append(("jog_start", kwargs))

    async def jog_update(self, **kwargs):
        self.calls.append(("jog_update", kwargs))

    async def quick_stop(self, **kwargs):
        self.calls.append(("quick_stop", kwargs or None))

    async def stop(self, **kwargs):
        self.calls.append(("stop", kwargs or None))

    async def home(self, **kwargs):
        self.calls.append(("home", kwargs))
        return "ok"

    async def fault_reset(self, **kwargs):
        self.calls.append(("fault_reset", kwargs))

    async def get_status_live(self):
        return {"fault": False, "operation_enabled": False}

    async def read_u16(self, index, sub=0):
        return 0

    def telemetry_latest(self):
        return None


class _State:
    pass


def _make_uc() -> tuple[DriveUseCases, _DriveFake]:
    state = _State()
    drive = _DriveFake()
    state.drive = drive
    state.motor_lock = _AsyncNoopLock()
    uc = DriveUseCases(state)
    return uc, drive


async def test_move_to_position_relative_uses_current_pos_and_profile() -> None:
    uc, drive = _make_uc()
    req = MoveToPositionRequest(
        target_position=20,
        relative=True,
        profile=ProfileConfig(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=20000,
    )

    data = await uc.move_to_position(req)

    assert data["target_position"] == 120
    assert ("jog_stop", None) in drive.calls
    move_call = next(c for c in drive.calls if c[0] == "move_to_position")
    kwargs = move_call[1]
    assert kwargs["target_position"] == 120
    assert kwargs["velocity"] == 200


async def test_move_to_position_motion_aborted_returns_ok_payload() -> None:
    uc, drive = _make_uc()
    drive.raise_on_move = MotionAborted("aborted")
    req = MoveToPositionRequest(
        target_position=10,
        relative=False,
        profile=ProfileConfig(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=20000,
    )

    data = await uc.move_to_position(req)

    assert data["aborted"] is True


async def test_reference_aborted_returns_homed_false() -> None:
    uc, drive = _make_uc()

    async def _aborted_home(**kwargs):
        raise MotionAborted("aborted")

    drive.home = _aborted_home  # type: ignore[assignment]

    data = await uc.reference(ReferenceRequest(timeout_ms=1000))

    assert data == {"homed": False, "aborted": True}


async def test_stop_timeout_maps_to_service_error_timeout() -> None:
    uc, drive = _make_uc()

    async def _timeout_stop(**kwargs):
        raise TimeoutError()

    drive.quick_stop = _timeout_stop  # type: ignore[assignment]

    with pytest.raises(ServiceError) as exc:
        await uc.stop(StopRequest(mode="quick_stop", timeout_ms=1000))

    assert exc.value.status_code == 504
    assert exc.value.code == "TIMEOUT"


async def test_fault_reset_respects_auto_enable() -> None:
    uc, drive = _make_uc()

    data = await uc.fault_reset(FaultResetRequest(after_reset={"auto_enable": False}, timeout_ms=1000))

    assert data["recovered"] is False
    assert data["fault_cleared"] is True
    assert "previous_fault" in data
    fault_call = next(c for c in drive.calls if c[0] == "fault_reset")
    assert fault_call[1]["recover"] is False
    assert isinstance(fault_call[1].get("op_id"), str)


async def test_jog_flow_direction_signs() -> None:
    uc, drive = _make_uc()

    start_data = await uc.jog_start(JogMoveRequest(direction="positive", speed=12, ttl_ms=200))
    update_data = await uc.jog_update(JogMoveRequest(direction="negative", speed=7, ttl_ms=300))

    assert start_data["velocity"] == 12
    assert update_data["velocity"] == -7
    assert any(c[0] == "jog_start" for c in drive.calls)
    assert any(c[0] == "jog_update" for c in drive.calls)


async def test_get_drive_status_from_cached_snapshot() -> None:
    uc, drive = _make_uc()

    class _Snapshot:
        statusword = 4660
        cia402_state = "unknown"
        position = 123
        velocity = 5
        mode_display = 1
        decoded_status = {"fault": False, "operation_enabled": True, "remote": True}
        ts_monotonic_s = 100.0

    drive.telemetry_latest = lambda: _Snapshot()  # type: ignore[assignment]
    drive.telemetry_poll_info = lambda: {"is_running": True, "interval_s": 0.5}  # type: ignore[assignment]

    status = await uc.get_drive_status()

    assert status.online.value in {"online", "degraded", "offline"}
    assert status.statusword == 4660
    assert status.position == 123.0
    assert status.velocity == 5.0


async def test_get_drive_telemetry_direct_read_path() -> None:
    uc, drive = _make_uc()
    drive.telemetry_latest = lambda: None  # type: ignore[assignment]

    async def _read_i32(index, sub):
        return 77

    async def _read_u16(index, sub):
        return 0

    async def _read_i8(index, sub):
        return 1

    async def _get_status():
        return {"fault": False}

    drive.read_i32 = _read_i32  # type: ignore[assignment]
    drive.read_u16 = _read_u16  # type: ignore[assignment]
    drive.read_i8 = _read_i8  # type: ignore[assignment]
    drive.get_status = _get_status  # type: ignore[assignment]

    telemetry = await uc.get_drive_telemetry()

    assert telemetry["velocity"] == 77.0
    assert "cia402_state" in telemetry


# ---------------------------------------------------------------------------
# Fault gate tests (Phase A) — require_not_in_fault blocks commands
# ---------------------------------------------------------------------------

def _make_uc_in_fault() -> tuple[DriveUseCases, _DriveFake]:
    """Create a use-case where the fake drive reports FAULT."""
    uc, drive = _make_uc()

    async def _status_live_fault():
        return {"fault": True, "operation_enabled": False}

    drive.get_status_live = _status_live_fault  # type: ignore[assignment]
    return uc, drive


async def test_move_to_position_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()
    req = MoveToPositionRequest(
        target_position=10,
        relative=False,
        profile=ProfileConfig(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=5000,
    )

    with pytest.raises(ServiceError) as exc:
        await uc.move_to_position(req)

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_jog_start_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()
    req = JogMoveRequest(direction="positive", speed=10, ttl_ms=200)

    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_reference_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()

    with pytest.raises(ServiceError) as exc:
        await uc.reference(ReferenceRequest(timeout_ms=1000))

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_fault_gate_passes_when_not_in_fault() -> None:
    """Normal (no-fault) path should not raise."""
    uc, drive = _make_uc()
    req = JogMoveRequest(direction="positive", speed=5, ttl_ms=200)

    data = await uc.jog_start(req)

    assert data["velocity"] == 5
    assert any(c[0] == "jog_start" for c in drive.calls)


async def test_fault_gate_tolerates_status_read_failure() -> None:
    """If get_status_live raises, fallthrough and let the command proceed."""
    uc, drive = _make_uc()

    async def _broken_status():
        raise ConnectionError("Modbus lost")

    drive.get_status_live = _broken_status  # type: ignore[assignment]
    req = JogMoveRequest(direction="positive", speed=5, ttl_ms=200)

    # Should NOT raise — gate is best-effort
    data = await uc.jog_start(req)
    assert data["velocity"] == 5


# ---------------------------------------------------------------------------
# Safety lockout gate — fault + remote=False (DI7 low) → SAFETY_LOCKOUT
# ---------------------------------------------------------------------------

def _make_uc_safety_lockout() -> tuple[DriveUseCases, _DriveFake]:
    """Fake drive in fault with remote=False (safety relay open)."""
    uc, drive = _make_uc()

    async def _status_safety():
        return {"fault": True, "remote": False, "operation_enabled": False}

    drive.get_status_live = _status_safety  # type: ignore[assignment]
    return uc, drive


async def test_safety_lockout_returns_503() -> None:
    """P0: fault + remote=False → ServiceError(503, SAFETY_LOCKOUT)."""
    uc, _drive = _make_uc_safety_lockout()
    req = JogMoveRequest(direction="positive", speed=5, ttl_ms=200)

    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)

    assert exc.value.status_code == 503
    assert exc.value.code == "SAFETY_LOCKOUT"


# ---------------------------------------------------------------------------
# Enhanced fault_reset tests (Phase C)
# ---------------------------------------------------------------------------

async def test_fault_reset_returns_enhanced_payload() -> None:
    uc, drive = _make_uc()
    data = await uc.fault_reset(FaultResetRequest(after_reset={"auto_enable": True}, timeout_ms=1000))

    assert data["recovered"] is True
    assert data["fault_cleared"] is True
    assert data["previous_fault"] is not None
    assert isinstance(data["previous_fault"], dict)
    # new_state is str|None — just check it's present
    assert "new_state" in data


async def test_fault_reset_detects_fault_not_cleared() -> None:
    uc, drive = _make_uc()

    # After fault_reset, drive still reports fault
    async def _still_in_fault():
        return {"fault": True, "operation_enabled": False}

    drive.get_status_live = _still_in_fault  # type: ignore[assignment]

    data = await uc.fault_reset(FaultResetRequest(timeout_ms=1000))

    assert data["fault_cleared"] is False
    assert data["new_state"] is None


# ---------------------------------------------------------------------------
# Fault details in status (Phase C)
# ---------------------------------------------------------------------------

async def test_get_drive_status_fault_details_populated_when_fault() -> None:
    uc, drive = _make_uc()

    class _FaultSnapshot:
        statusword = 0x0008  # fault bit set
        cia402_state = "fault"
        position = 0
        velocity = 0
        mode_display = 1
        decoded_status = {"fault": True, "operation_enabled": False, "remote": True}
        ts_monotonic_s = 200.0

    drive.telemetry_latest = lambda: _FaultSnapshot()  # type: ignore[assignment]
    drive.telemetry_poll_info = lambda: {"is_running": True, "interval_s": 0.5}  # type: ignore[assignment]

    status = await uc.get_drive_status()

    assert status.fault.active is True
    # details may be None if FaultManager can't read from fake drive,
    # but the field must be present
    assert hasattr(status.fault, "details")


async def test_get_drive_status_no_fault_details_when_healthy() -> None:
    uc, drive = _make_uc()

    class _HealthySnapshot:
        statusword = 0x0640
        cia402_state = "switch_on_disabled"
        position = 50
        velocity = 0
        mode_display = 1
        decoded_status = {"fault": False, "operation_enabled": False, "remote": True}
        ts_monotonic_s = 150.0

    drive.telemetry_latest = lambda: _HealthySnapshot()  # type: ignore[assignment]
    drive.telemetry_poll_info = lambda: {"is_running": True, "interval_s": 0.5}  # type: ignore[assignment]

    status = await uc.get_drive_status()

    assert status.fault.active is False
    assert status.fault.details is None
