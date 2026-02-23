from typing import Callable, Awaitable, TypeVar, Any, Type
from functools import wraps

T = TypeVar('T', bound=Callable[..., Awaitable[Any]])

from fastapi import HTTPException, status
from app.exceptions import (
    RobotBaseError,
    RobotError,
    DeviceBusyError,
    InputError,
    DeviceError,
    Conflict,
    DeviceReadyError,
    DeviceConnectionError,
    ErrorResponse,
)
ALLOWED_ERRORS = (DeviceBusyError, RobotError, Conflict, DeviceError, InputError, DeviceReadyError, DeviceConnectionError)
from app import state

def safe_getter(response_model: Type[Any]):
    def decorator(fn: Callable[..., Awaitable[Any]]):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            async with state.xarm_lock:
                try:
                    result = await fn(*args, **kwargs)
                    if not isinstance(result, response_model):
                        try:
                            return response_model(**result)
                        except Exception:
                            raise HTTPException(status_code=503, detail="Malformed result from getter")
                    return result
                except DeviceBusyError as e:
                    raise HTTPException(
                        status_code=status.HTTP_202_ACCEPTED,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_202_ACCEPTED).dict()
                    )
                except RobotError as e:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_422_UNPROCESSABLE_ENTITY).dict()
                    )
                except Conflict as e:
                    raise HTTPException(
                        status_code=status.HTTP_202_ACCEPTED,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_202_ACCEPTED).dict()
                    )
                except DeviceError as e:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_409_CONFLICT).dict()
                    )
                except DeviceReadyError as e:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_409_CONFLICT).dict()
                    )
                except DeviceConnectionError as e:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_503_SERVICE_UNAVAILABLE).dict()
                    )
                except InputError as e:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_404_NOT_FOUND).dict()
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=ErrorResponse(error=str(e), code=status.HTTP_500_INTERNAL_SERVER_ERROR).dict()
                    )
        return wrapper
    return decorator