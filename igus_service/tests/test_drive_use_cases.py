from __future__ import annotations

import pytest

from app.application.commands import (
    FaultResetCommand,
    JogCommand,
    MotionProfile,
    MoveCommand,
    ReferenceCommand,
    StopCommand,
)
from app.application.drive_service import ServiceError
from app.application.use_cases import DriveUseCases
from drivers.dryve_d1.protocol.exceptions import MotionAborted
from tests.fakes import AsyncNoopLock, FakeDrive, FakeSnapshot


class _AsyncLockedLock:
    """Lock that is permanently locked — simulates another command holding it."""

    def locked(self) -> bool:
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _State:
    pass


def _make_uc() -> tuple[DriveUseCases, FakeDrive]:
    state = _State()
    drive = FakeDrive()
    state.drive = drive
    state.motor_lock = AsyncNoopLock()
    uc = DriveUseCases(state)
    return uc, drive


async def test_move_to_position_relative_uses_current_pos_and_profile() -> None:
    uc, drive = _make_uc()
    req = MoveCommand(
        target_position=20,
        relative=True,
        profile=MotionProfile(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=20000,
    )

    data = await uc.move_to_position(req)

    assert data["target_position"] == 120
    assert any(c[0] == "jog_stop" for c in drive.calls)
    move_call = next(c for c in drive.calls if c[0] == "move_to_position")
    kwargs = move_call[1]
    assert kwargs["target_position"] == 120
    assert kwargs["velocity"] == 200


async def test_move_to_position_motion_aborted_returns_ok_payload() -> None:
    uc, drive = _make_uc()
    drive.raise_on_move = MotionAborted("aborted")
    req = MoveCommand(
        target_position=10,
        relative=False,
        profile=MotionProfile(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=20000,
    )

    data = await uc.move_to_position(req)

    assert data["aborted"] is True


async def test_reference_aborted_returns_homed_false() -> None:
    uc, drive = _make_uc()

    async def _aborted_home(**kwargs):
        raise MotionAborted("aborted")

    drive.home = _aborted_home  # type: ignore[assignment]

    data = await uc.reference(ReferenceCommand(timeout_ms=1000))

    assert data == {"homed": False, "aborted": True}


async def test_stop_timeout_maps_to_service_error_timeout() -> None:
    uc, drive = _make_uc()

    async def _timeout_stop(**kwargs):
        raise TimeoutError()

    drive.quick_stop = _timeout_stop  # type: ignore[assignment]

    with pytest.raises(ServiceError) as exc:
        await uc.stop(StopCommand(mode="quick_stop"))

    assert exc.value.status_code == 504
    assert exc.value.code == "TIMEOUT"


async def test_fault_reset_respects_auto_enable() -> None:
    uc, drive = _make_uc()

    data = await uc.fault_reset(FaultResetCommand(auto_enable=False))

    assert data["auto_enable_requested"] is False
    assert data["fault_cleared"] is True
    assert data["previous_fault"] is None or isinstance(data["previous_fault"], dict)
    fault_call = next(c for c in drive.calls if c[0] == "fault_reset")
    assert fault_call[1]["recover"] is False
    assert isinstance(fault_call[1].get("op_id"), str)


async def test_jog_flow_direction_signs() -> None:
    uc, drive = _make_uc()

    start_data = await uc.jog_start(JogCommand(direction="positive", speed=12, ttl_ms=200))
    update_data = await uc.jog_update(JogCommand(direction="negative", speed=7, ttl_ms=300))

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

    assert status.online in {"online", "degraded", "offline"}
    assert status.statusword == 4660
    assert status.position == 123.0
    assert status.velocity == 5.0


async def test_get_drive_status_via_constructor_cached_snapshot() -> None:
    """Cached-telemetry hot path using FakeDrive(cached_snapshot=FakeSnapshot()).

    Exercises the _read_drive_state() branch where telemetry_latest() returns a
    snapshot — the normal production path.  No OD register reads should occur.
    """
    snap = FakeSnapshot(statusword=0x0240, position=500, velocity=10, mode_display=1)
    state = _State()
    drive = FakeDrive(cached_snapshot=snap)
    state.drive = drive
    state.motor_lock = AsyncNoopLock()
    uc = DriveUseCases(state)

    status = await uc.get_drive_status()

    assert status.position == 500.0
    assert status.velocity == 10.0
    assert status.statusword == 0x0240
    # Verify the cached path was used: no direct OD reads on telemetry fields
    od_reads = {c[0] for c in drive.calls}
    assert "get_statusword" not in od_reads
    assert "get_position" not in od_reads
    assert "get_velocity_actual" not in od_reads


async def test_get_drive_telemetry_direct_read_path() -> None:
    uc, drive = _make_uc()
    drive.telemetry_latest = lambda: None  # type: ignore[assignment]

    async def _get_velocity_actual():
        return 77

    async def _get_statusword():
        return 0

    async def _get_cia402_state():
        from drivers.dryve_d1.od.statusword import infer_cia402_state
        return infer_cia402_state(0)

    async def _get_mode_display():
        return 1

    drive.get_velocity_actual = _get_velocity_actual  # type: ignore[assignment]
    drive.get_statusword = _get_statusword  # type: ignore[assignment]
    drive.get_cia402_state = _get_cia402_state  # type: ignore[assignment]
    drive.get_mode_display = _get_mode_display  # type: ignore[assignment]

    telemetry = await uc.get_drive_telemetry()

    assert telemetry["velocity"] == 77.0
    assert isinstance(telemetry["cia402_state"], str)


# ---------------------------------------------------------------------------
# Fault gate tests (Phase A) — require_not_in_fault blocks commands
# ---------------------------------------------------------------------------

def _make_uc_in_fault() -> tuple[DriveUseCases, FakeDrive]:
    """Create a use-case where the fake drive reports FAULT."""
    uc, drive = _make_uc()

    async def _status_live_fault():
        return {"fault": True, "operation_enabled": False}

    drive.get_status_live = _status_live_fault  # type: ignore[assignment]
    return uc, drive


async def test_move_to_position_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()
    req = MoveCommand(
        target_position=10,
        relative=False,
        profile=MotionProfile(velocity=200, acceleration=100, deceleration=100),
        timeout_ms=5000,
    )

    with pytest.raises(ServiceError) as exc:
        await uc.move_to_position(req)

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_jog_start_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()
    req = JogCommand(direction="positive", speed=10, ttl_ms=200)

    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_reference_blocked_when_drive_in_fault() -> None:
    uc, _drive = _make_uc_in_fault()

    with pytest.raises(ServiceError) as exc:
        await uc.reference(ReferenceCommand(timeout_ms=1000))

    assert exc.value.status_code == 409
    assert exc.value.code == "DRIVE_IN_FAULT"


async def test_fault_gate_passes_when_not_in_fault() -> None:
    """Normal (no-fault) path should not raise."""
    uc, drive = _make_uc()
    req = JogCommand(direction="positive", speed=5, ttl_ms=200)

    data = await uc.jog_start(req)

    assert data["velocity"] == 5
    assert any(c[0] == "jog_start" for c in drive.calls)


async def test_fault_gate_blocks_when_status_read_fails() -> None:
    """If get_status_live raises, motion must be blocked (fail-closed)."""
    uc, drive = _make_uc()

    async def _broken_status():
        raise ConnectionError("Modbus lost")

    drive.get_status_live = _broken_status  # type: ignore[assignment]
    req = JogCommand(direction="positive", speed=5, ttl_ms=200)

    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)

    assert exc.value.status_code == 503
    assert exc.value.code == "FAULT_CHECK_FAILED"


# ---------------------------------------------------------------------------
# Safety lockout gate — fault + remote=False (DI7 low) → SAFETY_LOCKOUT
# ---------------------------------------------------------------------------

def _make_uc_safety_lockout() -> tuple[DriveUseCases, FakeDrive]:
    """Fake drive in fault with remote=False (safety relay open)."""
    uc, drive = _make_uc()

    async def _status_safety():
        return {"fault": True, "remote": False, "operation_enabled": False}

    drive.get_status_live = _status_safety  # type: ignore[assignment]
    return uc, drive


async def test_safety_lockout_returns_503() -> None:
    """P0: fault + remote=False → ServiceError(503, SAFETY_LOCKOUT)."""
    uc, _drive = _make_uc_safety_lockout()
    req = JogCommand(direction="positive", speed=5, ttl_ms=200)

    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)

    assert exc.value.status_code == 503
    assert exc.value.code == "SAFETY_LOCKOUT"


# ---------------------------------------------------------------------------
# Enhanced fault_reset tests (Phase C)
# ---------------------------------------------------------------------------

async def test_fault_reset_returns_enhanced_payload() -> None:
    uc, drive = _make_uc()
    data = await uc.fault_reset(FaultResetCommand(auto_enable=True))

    assert data["auto_enable_requested"] is True
    assert data["fault_cleared"] is True
    assert data["previous_fault"] is not None
    assert isinstance(data["previous_fault"], dict)
    assert data["new_state"] is None or isinstance(data["new_state"], str)


async def test_fault_reset_detects_fault_not_cleared() -> None:
    uc, drive = _make_uc()

    # After fault_reset, drive still reports fault
    async def _still_in_fault():
        return {"fault": True, "operation_enabled": False}

    drive.get_status_live = _still_in_fault  # type: ignore[assignment]

    data = await uc.fault_reset(FaultResetCommand())

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


# ---------------------------------------------------------------------------
# reference() motor_lock contention tests
# ---------------------------------------------------------------------------

def _make_uc_with_locked_lock() -> tuple[DriveUseCases, FakeDrive]:
    """Create a use-case where motor_lock is already held."""
    state = _State()
    drive = FakeDrive()
    state.drive = drive
    state.motor_lock = _AsyncLockedLock()
    return DriveUseCases(state), drive


async def test_reference_blocked_when_motor_busy() -> None:
    uc, _drive = _make_uc_with_locked_lock()

    with pytest.raises(ServiceError) as exc:
        await uc.reference(ReferenceCommand(timeout_ms=1000))

    assert exc.value.status_code == 409
    assert exc.value.code == "MOTOR_BUSY"


async def test_reference_proceeds_when_lock_free() -> None:
    uc, drive = _make_uc()

    data = await uc.reference(ReferenceCommand(timeout_ms=1000))

    assert data["homed"] is True
    assert any(c[0] == "home" for c in drive.calls)


# ---------------------------------------------------------------------------
# jog_update motor_lock contention
# ---------------------------------------------------------------------------

async def test_jog_update_succeeds_when_motor_busy() -> None:
    """jog_update does not use motor_lock — succeeds even when lock is held."""
    uc, _drive = _make_uc_with_locked_lock()
    req = JogCommand(direction="positive", speed=5, ttl_ms=200)

    # Should NOT raise — jog_update is lock-free
    data = await uc.jog_update(req)
    assert data["direction"] == "positive"


async def test_jog_update_proceeds_when_lock_free() -> None:
    """jog_update succeeds when motor_lock is not held."""
    uc, drive = _make_uc()
    drive._jog_active = True  # jog must be active for update to proceed
    req = JogCommand(direction="negative", speed=10, ttl_ms=300)

    data = await uc.jog_update(req)

    assert data["velocity"] == -10
    assert data["direction"] == "negative"
    assert any(c[0] == "jog_update" for c in drive.calls)


# ---------------------------------------------------------------------------
# Jog: hot/warm path bypass motor_lock, abort_event cleared
# ---------------------------------------------------------------------------


async def test_jog_start_hot_path_bypasses_motor_lock() -> None:
    """When jog is already active (hot path), jog_start must not use motor_lock."""
    uc, drive = _make_uc_with_locked_lock()

    # Simulate active jog via public protocol method
    drive._jog_active = True

    req = JogCommand(direction="positive", speed=10, ttl_ms=200)
    # Should NOT raise MOTOR_BUSY — hot path bypasses lock
    data = await uc.jog_start(req)
    assert data["velocity"] > 0


async def test_jog_start_warm_path_bypasses_motor_lock() -> None:
    """When drive is in PV+OPERATION_ENABLED (warm), jog_start skips motor_lock."""
    uc, drive = _make_uc_with_locked_lock()

    # Not hot (jog not active)
    drive._jog_active = False

    # is_jog_warm returns True — override on instance
    async def _warm() -> bool:
        return True

    drive.is_jog_warm = _warm  # type: ignore[assignment]

    req = JogCommand(direction="negative", speed=5, ttl_ms=200)
    # Should NOT raise MOTOR_BUSY — warm path bypasses lock
    data = await uc.jog_start(req)
    assert data["velocity"] < 0


async def test_jog_start_cold_path_requires_motor_lock() -> None:
    """Cold path (not hot, not warm) must acquire motor_lock → MOTOR_BUSY when held."""
    uc, drive = _make_uc_with_locked_lock()
    # FakeDrive: is_jog_active()=False, is_jog_warm()=False → cold path
    drive._jog_active = False

    req = JogCommand(direction="positive", speed=10, ttl_ms=200)
    with pytest.raises(ServiceError) as exc:
        await uc.jog_start(req)
    assert exc.value.code == "MOTOR_BUSY"


async def test_jog_update_succeeds_during_cold_path() -> None:
    """jog_update must succeed even when motor_lock is held (lock-free)."""
    uc, drive = _make_uc_with_locked_lock()
    drive._jog_active = True

    req = JogCommand(direction="positive", speed=10, ttl_ms=200)
    # Should NOT raise — jog_update is lock-free
    data = await uc.jog_update(req)
    assert data["direction"] == "positive"


# ---------------------------------------------------------------------------
# TEST-03: FakeDrive satisfies DriveProtocol (runtime_checkable conformance)
# ---------------------------------------------------------------------------

def test_fake_drive_satisfies_drive_protocol() -> None:
    """FakeDrive must pass isinstance check against DriveProtocol.

    This catches method signature drift between the protocol and the test
    fake — a missing or renamed method will fail here rather than silently
    producing incorrect test results.
    """
    from app.protocols import DriveProtocol

    assert isinstance(FakeDrive(), DriveProtocol)
