"""Tests for concurrent motion lock contention and _try_acquire semantics."""

from __future__ import annotations

import asyncio

import pytest

from app.application.use_cases import _try_acquire


# ---------------------------------------------------------------------------
# TEST-03: _try_acquire under real contention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_acquire_succeeds_on_free_lock() -> None:
    lock = asyncio.Lock()
    assert _try_acquire(lock) is True
    assert lock.locked()
    lock.release()


@pytest.mark.asyncio
async def test_try_acquire_returns_false_when_held() -> None:
    """Real asyncio.Lock contention: _try_acquire must return False."""
    lock = asyncio.Lock()
    await lock.acquire()
    try:
        assert _try_acquire(lock) is False
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_try_acquire_two_tasks_contention() -> None:
    """Two tasks compete for same lock — second must get False."""
    lock = asyncio.Lock()
    results: list[bool] = []

    async def holder() -> None:
        assert _try_acquire(lock) is True
        results.append(True)
        await asyncio.sleep(0.05)
        lock.release()

    async def contender() -> None:
        await asyncio.sleep(0.01)  # let holder grab lock first
        results.append(_try_acquire(lock))

    await asyncio.gather(holder(), contender())
    assert results == [True, False]


# ---------------------------------------------------------------------------
# TEST-01: Concurrent motion → MOTOR_BUSY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_jog_returns_motor_busy() -> None:
    """Jog while move holds the lock → MOTOR_BUSY via _non_queuing_lock."""
    from app.application.commands import JogCommand, MotionProfile, MoveCommand
    from app.application.drive_service import ServiceError
    from app.application.use_cases import DriveUseCases
    from tests.fakes import FakeDrive

    class _SlowFakeDrive(FakeDrive):
        async def move_to_position(self, **kwargs) -> None:  # type: ignore[override]
            self.calls.append(("move_to_position", kwargs))
            await asyncio.sleep(0.2)  # simulate slow motion

    class _State:
        pass

    state = _State()
    drive = _SlowFakeDrive()
    state.drive = drive  # type: ignore[attr-defined]
    state.motor_lock = asyncio.Lock()  # real lock, not noop  # type: ignore[attr-defined]
    uc = DriveUseCases(state)  # type: ignore[arg-type]

    move_cmd = MoveCommand(
        target_position=1000,
        relative=False,
        profile=MotionProfile(velocity=200, acceleration=100, deceleration=100),
    )
    jog_cmd = JogCommand(direction="positive", speed=5, ttl_ms=200)

    results: list[str] = []

    async def do_move() -> None:
        await uc.move_to_position(move_cmd)
        results.append("move_ok")

    async def do_jog() -> None:
        await asyncio.sleep(0.05)  # let move acquire lock first
        try:
            await uc.jog_start(jog_cmd)
            results.append("jog_ok")
        except ServiceError as exc:
            results.append(exc.code)

    await asyncio.gather(do_move(), do_jog())
    assert "MOTOR_BUSY" in results


# ---------------------------------------------------------------------------
# TEST-02: Persistent Modbus failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_drive_persistent_failure() -> None:
    """FakeDrive with fail_count raises OSError for first N read_u16 calls."""
    from tests.fakes import FakeDrive

    drive = FakeDrive(fail_count=3)
    with pytest.raises(OSError):
        await drive.read_u16(0x6041, 0)
    with pytest.raises(OSError):
        await drive.read_u16(0x6041, 0)
    with pytest.raises(OSError):
        await drive.read_u16(0x6041, 0)
    # Fourth call succeeds
    result = await drive.read_u16(0x6041, 0)
    assert result == 0
