"""Tests for routes/decorators.py — _TaskManager, safe_getter, tasked_getter."""

import asyncio
import pytest
from unittest.mock import AsyncMock


# ── _jsonable helper ──────────────────────────────────────────────
def test_jsonable_pydantic_v2():
    from routes.decorators import _jsonable
    from unittest.mock import MagicMock
    obj = MagicMock()
    obj.model_dump = MagicMock(return_value={"a": 1})
    assert _jsonable(obj) == {"a": 1}


def test_jsonable_simple_namespace():
    from routes.decorators import _jsonable
    from types import SimpleNamespace
    obj = SimpleNamespace(x=1, y=SimpleNamespace(z=2))
    result = _jsonable(obj)
    assert result == {"x": 1, "y": {"z": 2}}


def test_jsonable_list_tuple():
    from routes.decorators import _jsonable
    assert _jsonable([1, 2, 3]) == [1, 2, 3]
    assert _jsonable((4, 5)) == [4, 5]


def test_jsonable_dict():
    from routes.decorators import _jsonable
    assert _jsonable({"a": 1}) == {"a": 1}


def test_jsonable_scalar():
    from routes.decorators import _jsonable
    assert _jsonable(42) == 42
    assert _jsonable("hey") == "hey"


# ── _TaskManager ──────────────────────────────────────────────────
@pytest.fixture
def tm():
    """Fresh TaskManager instance for each test."""
    from routes.decorators import _TaskManager
    return _TaskManager()


@pytest.mark.asyncio
async def test_task_start_and_finish(tm):
    async def _work():
        return {"ok": True}

    tid = await tm.start(_work)
    assert isinstance(tid, str) and len(tid) == 12

    # Wait for completion
    await asyncio.sleep(0.05)
    status = tm.status(tid)
    assert status.status.value == "finished"
    assert status.result is not None


@pytest.mark.asyncio
async def test_task_rejects_duplicate(tm):
    """Starting a new task while one is running raises DeviceBusyError."""
    from exceptions import DeviceBusyError

    event1 = asyncio.Event()

    async def _slow1():
        await event1.wait()

    tid1 = await tm.start(_slow1)
    await asyncio.sleep(0.01)
    assert tm.is_busy()

    # Second start should be rejected (no preemption)
    with pytest.raises(DeviceBusyError, match="already running"):
        await tm.start(lambda: _slow1())

    # Original task is still running
    assert tm.is_busy()
    assert tm.current_id() == tid1

    # Clean up
    event1.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_task_error_status(tm):
    async def _fail():
        raise RuntimeError("boom")

    tid = await tm.start(_fail)
    await asyncio.sleep(0.05)
    status = tm.status(tid)
    assert status.status.value == "error"
    assert "boom" in status.result["error"]


@pytest.mark.asyncio
async def test_task_cancel(tm):
    event = asyncio.Event()

    async def _hang():
        await event.wait()

    tid = await tm.start(_hang)
    await asyncio.sleep(0.01)

    result = await tm.cancel(tid)
    # May be cancelled or working (if cancel is slow)
    assert result.status.value in ("cancelled", "working")

    event.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_task_cancel_wrong_id(tm):
    result = await tm.cancel("nonexistent")
    assert result.status.value == "not_found"


@pytest.mark.asyncio
async def test_task_cancel_already_done(tm):
    async def _fast():
        return 42

    tid = await tm.start(_fast)
    await asyncio.sleep(0.05)
    result = await tm.cancel(tid)
    assert result.status.value == "finished"


def test_task_status_wrong_id(tm):
    result = tm.status("wrong")
    assert result.status.value == "not_found"


def test_task_is_busy_initially(tm):
    assert tm.is_busy() is False
    assert tm.current_id() is None


# ── safe_getter decorator ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_safe_getter_success():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse

    @safe_getter(RobotActionResponse)
    async def _handler():
        return {"success": True, "detail": "done"}

    result = await _handler()
    assert isinstance(result, RobotActionResponse)
    assert result.success is True


@pytest.mark.asyncio
async def test_safe_getter_device_busy():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import DeviceBusyError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise DeviceBusyError("busy")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 202


@pytest.mark.asyncio
async def test_safe_getter_robot_error():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import RobotError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise RobotError("broken")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_safe_getter_device_connection_error():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import DeviceConnectionError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise DeviceConnectionError("unreachable")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_safe_getter_input_error():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import InputError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise InputError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_safe_getter_conflict():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import Conflict
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise Conflict("conflict")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 202


@pytest.mark.asyncio
async def test_safe_getter_device_error():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import DeviceError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise DeviceError("broken device")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_safe_getter_device_ready_error():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from exceptions import DeviceReadyError
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise DeviceReadyError("not ready")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_safe_getter_unknown_exception():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        raise ValueError("unexpected")

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_safe_getter_malformed_result():
    from routes.decorators import safe_getter
    from models.api_types import RobotActionResponse
    from fastapi import HTTPException

    @safe_getter(RobotActionResponse)
    async def _handler():
        return {"not_a_valid_field_zzzz": True}  # can't be converted to RobotActionResponse

    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 503  # Malformed result → 503


# ── tasked_getter decorator ───────────────────────────────────────
@pytest.mark.asyncio
async def test_tasked_getter_starts_task():
    from routes.decorators import tasked_getter, task_manager
    from models.api_types import RobotActionResponse

    # Ensure no leftover tasks
    if task_manager.is_busy():
        await asyncio.sleep(0.1)

    @tasked_getter(RobotActionResponse)
    async def _handler():
        return True

    result = await _handler()
    assert isinstance(result, RobotActionResponse)
    assert result.success is True
    assert result.task_id is not None
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_tasked_getter_rejects_duplicate():
    """tasked_getter: second call while busy returns HTTP 202 (busy)."""
    from routes.decorators import tasked_getter, task_manager
    from models.api_types import RobotActionResponse
    from fastapi import HTTPException

    event1 = asyncio.Event()

    @tasked_getter(RobotActionResponse)
    async def _handler():
        await event1.wait()

    # Start first task
    resp1 = await _handler()
    assert resp1.success is True

    # Second call should be rejected with 202
    with pytest.raises(HTTPException) as exc_info:
        await _handler()
    assert exc_info.value.status_code == 202

    # Clean up
    event1.set()
    await asyncio.sleep(0.05)


# ── _TaskManager phase info in status ────────────────────────────


@pytest.mark.asyncio
async def test_task_status_includes_phase_while_working(tm):
    """While a task is WORKING, status should include phase info."""
    from unittest.mock import patch

    event = asyncio.Event()

    async def _work():
        await event.wait()

    tid = await tm.start(_work)
    await asyncio.sleep(0.01)

    # Patch robot_scripts to return a known phase
    with patch("app.robot_scripts.get_task_phase", return_value={"phase": "navigate", "task_id": tid}):
        status = tm.status(tid)
        assert status.status.value == "working"
        assert status.result is not None
        assert status.result["phase"] == "navigate"

    event.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_task_runner_sets_phase_task_id(tm):
    """_runner sets phase task_id to the TaskManager's task_id."""
    from unittest.mock import patch
    import app.robot_scripts as rs

    captured_task_id = None
    orig_set_phase = rs._set_phase

    def _capture(phase, *, task_id=None):
        nonlocal captured_task_id
        if task_id is not None:
            captured_task_id = task_id
        orig_set_phase(phase, task_id=task_id)

    async def _work():
        return "done"

    with patch.object(rs, "_set_phase", side_effect=_capture):
        tid = await tm.start(_work)
        await asyncio.sleep(0.05)

    assert captured_task_id == tid, "TaskManager should propagate task_id to phase tracker"
