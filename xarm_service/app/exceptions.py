
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class ErrorResponse(BaseModel):
    error: str
    code: Optional[int] = None
    details: Optional[Dict[str, Any]] = None

class RobotBaseError(Exception):
    """Базовый класс для всех ошибок робота."""
    pass

class RobotError(RobotBaseError):
    pass

class DeviceBusyError(RobotBaseError):
    pass
class DeviceConnectionError(RobotBaseError):
    """Ошибка подключения к устройству."""
    pass
class DeviceError(RobotBaseError):
    pass

class Conflict(RobotBaseError):
    pass

class InputError(RobotBaseError):
    pass

class DeviceReadyError(RobotBaseError):
    pass


class TransportMoveError(Exception):
    pass

