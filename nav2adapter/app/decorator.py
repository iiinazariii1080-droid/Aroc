import asyncio
from functools import wraps
from typing import Callable, Union
from fastapi.concurrency import run_in_threadpool
import inspect
from exceptions import DeviceBusyError,RobotBaseError
import logging

_LOGGER = logging.getLogger(__name__)

def safe_call(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await run_in_threadpool(func, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except RobotBaseError:
            raise
        except Exception as e:
            _LOGGER.error("Robot command failed in %s: %s", func.__name__, e, exc_info=True)
            raise RuntimeError(f"Robot command failed: {e}") from e
    return wrapper


def guarded_async_call(lock_or_attr: Union[asyncio.Lock, str], *, timeout_s: float = 0.3):
    """Serialize access to a robot command behind an asyncio.Lock.

    ``lock_or_attr`` may be:
    * an ``asyncio.Lock`` instance (legacy — resolved at decoration time), or
    * a ``str`` attribute name (resolved at call time via ``getattr(self, attr)``).
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            import time
            # Resolve lock: string → instance attribute, Lock → use directly
            if isinstance(lock_or_attr, str):
                lock = getattr(args[0], lock_or_attr)
            else:
                lock = lock_or_attr
            start_wait = time.time()
            try:
                await asyncio.wait_for(lock.acquire(), timeout=float(timeout_s))
                wait_time = time.time() - start_wait
                if wait_time > 0.1:  # Log if we had to wait more than 100ms
                    _LOGGER.debug("Acquired lock for %s after %.3fs wait", func.__name__, wait_time)
            except asyncio.TimeoutError:
                _LOGGER.warning("Failed to acquire lock for %s within %ss timeout", func.__name__, timeout_s)
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
        raise
    except RobotBaseError:
        raise
    except Exception as e:
        _LOGGER.error("Robot command failed in %s: %s", func.__name__, e, exc_info=True)
        raise RuntimeError(f"{func.__name__} failed: {e}") from e
