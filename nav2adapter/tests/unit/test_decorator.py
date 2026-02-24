"""Tests for app/decorator.py — safe_call and guarded_async_call."""

import asyncio
import pytest
from unittest.mock import AsyncMock
from app.decorator import safe_call, guarded_async_call, _execute_robot_command
from exceptions import DeviceBusyError, RobotBaseError


# ── safe_call ────────────────────────────────────────────────────
class TestSafeCall:
    @pytest.mark.asyncio
    async def test_async_success(self):
        @safe_call
        async def _fn():
            return 42

        assert await _fn() == 42

    @pytest.mark.asyncio
    async def test_sync_success(self):
        @safe_call
        def _fn():
            return "ok"

        assert await _fn() == "ok"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        @safe_call
        async def _fn():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _fn()

    @pytest.mark.asyncio
    async def test_robot_base_error_propagates(self):
        @safe_call
        async def _fn():
            raise RobotBaseError("robot error")

        with pytest.raises(RobotBaseError):
            await _fn()

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped(self):
        @safe_call
        async def _fn():
            raise ValueError("bad value")

        with pytest.raises(RuntimeError, match="Robot command failed"):
            await _fn()


# ── guarded_async_call ───────────────────────────────────────────
class TestGuardedAsyncCall:
    @pytest.mark.asyncio
    async def test_success_with_lock(self):
        lock = asyncio.Lock()

        @guarded_async_call(lock)
        async def _fn():
            return "done"

        assert await _fn() == "done"

    @pytest.mark.asyncio
    async def test_busy_when_locked(self):
        lock = asyncio.Lock()
        await lock.acquire()  # hold the lock

        @guarded_async_call(lock, timeout_s=0.05)
        async def _fn():
            return "done"

        with pytest.raises(DeviceBusyError):
            await _fn()

        lock.release()

    @pytest.mark.asyncio
    async def test_releases_on_error(self):
        lock = asyncio.Lock()

        @guarded_async_call(lock)
        async def _fn():
            raise ValueError("oops")

        with pytest.raises(RuntimeError):
            await _fn()

        # Lock should be released
        assert not lock.locked()


# ── _execute_robot_command ───────────────────────────────────────
class TestExecuteRobotCommand:
    @pytest.mark.asyncio
    async def test_async_function(self):
        async def _fn():
            return 99
        assert await _execute_robot_command(_fn) == 99

    @pytest.mark.asyncio
    async def test_sync_function(self):
        def _fn():
            return 77
        assert await _execute_robot_command(_fn) == 77

    @pytest.mark.asyncio
    async def test_cancelled_error(self):
        async def _fn():
            raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await _execute_robot_command(_fn)

    @pytest.mark.asyncio
    async def test_robot_base_error(self):
        async def _fn():
            raise RobotBaseError("err")
        with pytest.raises(RobotBaseError):
            await _execute_robot_command(_fn)

    @pytest.mark.asyncio
    async def test_generic_wrapped(self):
        async def _fn():
            raise TypeError("nope")
        with pytest.raises(RuntimeError, match="failed"):
            await _execute_robot_command(_fn)
