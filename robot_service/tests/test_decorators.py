"""Tests for app/decorator.py — safe_call, guarded_async_call, _execute_robot_command."""

import asyncio
import pytest
from unittest.mock import AsyncMock


# ── safe_call ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_safe_call_async_success():
    from app.decorator import safe_call

    @safe_call
    async def _action():
        return 42

    assert await _action() == 42


@pytest.mark.asyncio
async def test_safe_call_sync_success():
    from app.decorator import safe_call

    @safe_call
    def _action():
        return 99

    result = await _action()
    assert result == 99


@pytest.mark.asyncio
async def test_safe_call_robot_error_propagates():
    from app.decorator import safe_call
    from exceptions import RobotError

    @safe_call
    async def _action():
        raise RobotError("test")

    with pytest.raises(RobotError):
        await _action()


@pytest.mark.asyncio
async def test_safe_call_generic_exception_wrapped():
    from app.decorator import safe_call

    @safe_call
    async def _action():
        raise ValueError("oops")

    with pytest.raises(RuntimeError, match="Robot command failed"):
        await _action()


# ── guarded_async_call ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_guarded_async_call_success():
    from app.decorator import guarded_async_call

    lock = asyncio.Lock()

    @guarded_async_call(lock)
    async def _action():
        return "done"

    assert await _action() == "done"
    assert not lock.locked()


@pytest.mark.asyncio
async def test_guarded_async_call_busy():
    from app.decorator import guarded_async_call
    from exceptions import DeviceBusyError

    lock = asyncio.Lock()
    await lock.acquire()  # Pre-lock

    @guarded_async_call(lock)
    async def _action():
        return "done"

    with pytest.raises(DeviceBusyError):
        await _action()

    lock.release()


@pytest.mark.asyncio
async def test_guarded_async_call_releases_on_error():
    from app.decorator import guarded_async_call

    lock = asyncio.Lock()

    @guarded_async_call(lock)
    async def _action():
        raise ValueError("fail")

    with pytest.raises(RuntimeError, match="failed"):
        await _action()

    # Lock should be released even after error
    assert not lock.locked()


# ── _execute_robot_command ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_execute_robot_command_async():
    from app.decorator import _execute_robot_command

    async def _fn():
        return "ok"

    result = await _execute_robot_command(_fn)
    assert result == "ok"


@pytest.mark.asyncio
async def test_execute_robot_command_sync():
    from app.decorator import _execute_robot_command

    def _fn():
        return "sync_ok"

    result = await _execute_robot_command(_fn)
    assert result == "sync_ok"


@pytest.mark.asyncio
async def test_execute_robot_command_cancelled():
    from app.decorator import _execute_robot_command

    async def _fn():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _execute_robot_command(_fn)


@pytest.mark.asyncio
async def test_execute_robot_command_robot_error():
    from app.decorator import _execute_robot_command
    from exceptions import RobotError

    async def _fn():
        raise RobotError("test")

    with pytest.raises(RobotError):
        await _execute_robot_command(_fn)


@pytest.mark.asyncio
async def test_execute_robot_command_generic():
    from app.decorator import _execute_robot_command

    async def _fn():
        raise ValueError("oops")

    with pytest.raises(RuntimeError, match="failed"):
        await _execute_robot_command(_fn)
