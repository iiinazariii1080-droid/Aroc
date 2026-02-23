import asyncio
from functools import wraps
from typing import Callable
from fastapi.concurrency import run_in_threadpool
import inspect
from exceptions import DeviceBusyError,RobotBaseError

def safe_call(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await run_in_threadpool(func, *args, **kwargs)
        except RobotBaseError:
            raise
        except Exception as e:
            print(f"[Robot ERROR] {func.__name__}: {e}")
            raise RuntimeError(f"Robot command failed: {e}") from e
    return wrapper


def guarded_async_call(lock: asyncio.Lock):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.3)
            except asyncio.TimeoutError:
                raise DeviceBusyError("Device is busy")
            try:
                return await _execute_robot_command(func, *args, **kwargs)
            finally:
                lock.release()
        return wrapper
    return decorator


async def _execute_robot_command(func: Callable, *args, **kwargs):
    try:
        if inspect.iscoroutinefunction(func):
            result = await func(*args, **kwargs)
        else:
            result = await run_in_threadpool(func, *args, **kwargs)
        return result
    except asyncio.CancelledError:
        # propagate cancellation, do not wrap
        raise
    except RobotBaseError:
        raise
    except Exception as e:
        raise RuntimeError(f"{func.__name__} failed: {e}")
