import logging
from typing import Any, Awaitable, Callable, Type, TypeVar
from functools import wraps
from fastapi import HTTPException, status

from exceptions import DeviceBusyError, RobotBaseError, RobotError, InputError, DeviceError, Conflict, DeviceReadyError, DeviceConnectionError
ALLOWED_ERRORS = (DeviceBusyError, RobotError, Conflict, DeviceError, InputError, DeviceReadyError, DeviceConnectionError)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Awaitable[Any]])

def _err(msg: str, *, err_type: str = "Error") -> dict:
    # Keep a consistent error envelope: detail.error is always an object (dict)
    return {"error": {"type": err_type, "msg": msg}}

def safe_getter(response_model: Type[Any]):
    """
    Декоратор для геттер-эндпоинтов: ловит любые ошибки, всегда возвращает response_model или ErrorStatus.
    """
    def decorator(fn: Callable[..., Awaitable[Any]]):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await fn(*args, **kwargs)
                if not isinstance(result, response_model):
                    try:
                        if hasattr(result, "model_dump"):
                            result = result.model_dump()
                        elif hasattr(result, "dict"):
                            result = result.dict()
                        return response_model(**result)
                    except Exception:
                        raise HTTPException(status_code=503, detail=_err("Malformed result from getter", err_type="MalformedResult"))
                return result
            except HTTPException as e:
                # Preserve HTTPException raised inside the handler
                raise e
            except DeviceBusyError as e:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=_err(str(e), err_type="DeviceBusy")
                )
            except RobotError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=_err(str(e), err_type="RobotError")
                )
            except Conflict as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_err(str(e), err_type="Conflict")
                )
            except DeviceError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_err(str(e), err_type="DeviceError")
                )
            except DeviceReadyError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_err(str(e), err_type="DeviceReadyError")
                )
            except DeviceConnectionError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=_err(str(e), err_type="DeviceConnectionError")
                )
            except InputError as e:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=_err(str(e), err_type="InputError")
                )
            except Exception as e:
                _LOGGER.error("Unhandled exception in %s: %s", getattr(fn, "__name__", "handler"), e, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=_err("Internal server error", err_type="InternalError")
                )
        return wrapper
    return decorator