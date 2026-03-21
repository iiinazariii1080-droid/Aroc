import os

from fastapi import HTTPException
from models.api_types import RobotActionResponse, TaskStatusResponse
from models.base_types import TaskStatus
from typing import Any, Type, Optional, Dict, Callable, Awaitable, TypeVar

from functools import wraps

T = TypeVar('T', bound=Callable[..., Awaitable[Any]])
from fastapi import HTTPException, status

from exceptions import DeviceBusyError, RobotBaseError, RobotError, InputError, DeviceError, Conflict, DeviceReadyError, DeviceConnectionError, SafetyLockoutError
ALLOWED_ERRORS = (DeviceBusyError, RobotError, Conflict, DeviceError, InputError, DeviceReadyError, DeviceConnectionError, SafetyLockoutError)


def _raise_http_for_domain_error(exc: Exception, *, extra_detail: dict | None = None) -> None:
    """Map domain exceptions to HTTPException. Shared by safe_getter / tasked_getter."""
    detail: dict = {"error": str(exc)}
    if extra_detail:
        detail.update(extra_detail)

    if isinstance(exc, DeviceBusyError):
        raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=detail)
    if isinstance(exc, SafetyLockoutError):
        detail["safety_lockout"] = True
        raise HTTPException(status_code=423, detail=detail)
    if isinstance(exc, RobotError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    if isinstance(exc, Conflict):
        raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=detail)
    if isinstance(exc, DeviceReadyError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(exc, DeviceError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(exc, DeviceConnectionError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    if isinstance(exc, InputError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


def safe_getter(response_model: Type[Any]):
    def decorator(fn: Callable[..., Awaitable[Any]]):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await fn(*args, **kwargs)
                if not isinstance(result, response_model):
                    try:
                        return response_model(**result)
                    except Exception:
                        raise HTTPException(status_code=503, detail="Malformed result from getter")
                return result
            except HTTPException:
                raise
            except Exception as exc:
                _raise_http_for_domain_error(exc)
        return wrapper
    return decorator

# ============================================================
# Simple in-process Task Manager (single-task, exclusive)
# ============================================================
import asyncio
import uuid
from types import SimpleNamespace

def _jsonable(obj: Any) -> Any:
    try:
        # pydantic v2
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        # pydantic v1
        if hasattr(obj, "dict"):
            return obj.dict()
        if isinstance(obj, SimpleNamespace):
            return {k: _jsonable(v) for k, v in obj.__dict__.items()}
        if isinstance(obj, (list, tuple)):
            return [_jsonable(v) for v in obj]
        if isinstance(obj, dict):
            return {k: _jsonable(v) for k, v in obj.items()}
        return obj
    except Exception:
        return str(obj)

class _TaskManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._task_id: Optional[str] = None
        self._status: TaskStatus = TaskStatus.NOT_FOUND
        self._result: Optional[Any] = None
        self._error: Optional[str] = None

    def is_busy(self) -> bool:
        return bool(self._task and not self._task.done())

    def current_id(self) -> Optional[str]:
        return self._task_id if self.is_busy() else None

    async def start(self, coro_factory) -> str:
        async with self._lock:
            if self.is_busy():
                # DO NOT cancel + preempt here.  The previous implementation
                # cancelled the running task and started a new one, but this
                # caused a critical bug: when the frontend retried a navigate
                # request while POSITIONING was in progress (lift raising,
                # arm moving), the cancel killed the physical motion mid-way
                # and the robot was left in an unsafe intermediate state.
                # Instead, reject the duplicate — the frontend should poll
                # /tasks/status/{task_id} and wait for completion.
                raise DeviceBusyError("Task already running")

            self._result = None
            self._error = None
            self._status = TaskStatus.PENDING
            self._task_id = uuid.uuid4().hex[:12]

            async def _runner():
                self._status = TaskStatus.WORKING
                # Propagate task_id to phase tracker for correlation
                try:
                    import app.robot_scripts as _rs
                    _rs._set_phase(_rs.TaskPhase.IDLE, task_id=self._task_id)
                except Exception:
                    pass
                try:
                    res = await coro_factory()
                    self._result = _jsonable(res)
                    self._status = TaskStatus.FINISHED
                except asyncio.CancelledError:
                    self._error = "cancelled"
                    self._status = TaskStatus.CANCELLED
                    raise
                except Exception as e:
                    _safety_logger.error("Task %s failed: %s", self._task_id, e, exc_info=True)
                    self._error = str(e)
                    self._status = TaskStatus.ERROR
                finally:
                    # clear reference after completion
                    self._task = None

            self._task = asyncio.create_task(_runner())
            return self._task_id

    def status(self, task_id: Optional[str]) -> TaskStatusResponse:
        if not task_id or task_id != self._task_id:
            return TaskStatusResponse(status=TaskStatus.NOT_FOUND, result=None)
        # Provide a lightweight result only when finished
        if self._status == TaskStatus.FINISHED:
            return TaskStatusResponse(status=self._status, result={"result": _jsonable(self._result)})
        if self._status == TaskStatus.ERROR:
            return TaskStatusResponse(status=self._status, result={"error": self._error})
        if self._status == TaskStatus.CANCELLED:
            return TaskStatusResponse(status=self._status, result={"error": self._error})
        # Include phase info for in-progress tasks
        phase_info = None
        try:
            import app.robot_scripts as _rs
            phase_info = _rs.get_task_phase()
        except Exception:
            pass
        return TaskStatusResponse(status=self._status, result=phase_info)

    async def cancel(self, task_id: Optional[str]) -> TaskStatusResponse:
        if not task_id or task_id != self._task_id:
            return TaskStatusResponse(status=TaskStatus.NOT_FOUND, result=None)
        if not self._task:
            return TaskStatusResponse(status=self._status, result=None)
        if self._task.done():
            # Already finished/error/cancelled
            return self.status(task_id)
        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            # Still cancelling, report working to caller
            return TaskStatusResponse(status=TaskStatus.WORKING, result=None)
        except asyncio.CancelledError:
            pass
        return self.status(task_id)


task_manager = _TaskManager()


import logging as _logging

_safety_logger = _logging.getLogger("robot_service.safety")


async def check_safety_lockout() -> None:
    """Pre-flight check: raises SafetyLockoutError if motion is unsafe.

    Delegates to the centralized SafetyKernel (safety/safety_kernel.py).
    """
    from safety.safety_kernel import get_safety_kernel
    await get_safety_kernel().authorize_motion_force("task_start")


def tasked_getter(response_model: Type[Any]):
    """Decorator: runs wrapped async function as an exclusive background task.

    - If a task is already running, raises DeviceBusyError (handled below to 202).
    - Immediately returns RobotActionResponse with task_id and success flag.
    - Result can be polled via task status endpoints.
    """
    def decorator(fn: Callable[..., Awaitable[Any]]):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                await check_safety_lockout()

                async def _factory():
                    return await fn(*args, **kwargs)

                task_id = await task_manager.start(_factory)
                # Return a standard async response envelope
                if response_model is RobotActionResponse or response_model is Any:
                    return RobotActionResponse(success=True, task_id=task_id, detail="working")
                try:
                    return response_model(success=True, task_id=task_id, detail="working")
                except Exception:
                    return RobotActionResponse(success=True, task_id=task_id, detail="working")
            except HTTPException:
                raise
            except DeviceBusyError as exc:
                _raise_http_for_domain_error(exc, extra_detail={"task_id": task_manager.current_id()})
            except Exception as exc:
                _raise_http_for_domain_error(exc)
        return wrapper
    return decorator