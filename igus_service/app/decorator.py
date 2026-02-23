from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

T = TypeVar('T', bound=Callable[..., Awaitable[Any]])

from fastapi import HTTPException, status

from app.http_errors import error_detail
from app.models import (
    Conflict,
    DeviceBusyError,
    DeviceConnectionError,
    DeviceError,
    DeviceReadyError,
    InputError,
    RobotError,
)


def safe_getter(response_model: type[Any]):
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
                        return response_model(**result)
                    except Exception as exc:
                        raise HTTPException(status_code=503, detail="Malformed result from getter") from exc
                return result
            except DeviceBusyError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=error_detail("DEVICE_BUSY", str(e))
                ) from e
            except RobotError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=error_detail("ROBOT_ERROR", str(e))
                ) from e
            except Conflict as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=error_detail("CONFLICT", str(e))
                ) from e
            except DeviceError as e:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=error_detail("DEVICE_ERROR", str(e))
                ) from e
            except DeviceReadyError as e:
                raise HTTPException(
                    status_code=status.HTTP_412_PRECONDITION_FAILED,
                    detail=error_detail("DEVICE_NOT_READY", str(e))
                ) from e
            except DeviceConnectionError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=error_detail("DRIVE_UNAVAILABLE", str(e))
                ) from e
            except InputError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_detail("INVALID_INPUT", str(e))
                ) from e
            except Exception as e:
                # Map protocol/transport errors to 503 (service unavailable)
                try:
                    from drivers.dryve_d1.protocol.exceptions import ProtocolError
                except Exception:
                    ProtocolError = None  # type: ignore

                if ProtocolError is not None and isinstance(e, ProtocolError):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=error_detail("PROTOCOL_ERROR", str(e))
                    ) from e

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=error_detail("INTERNAL_ERROR", str(e))
                ) from e
        return wrapper
    return decorator