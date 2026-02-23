"""
base_types.py - Базовые типы и константы для стандартизации типизации

Этот модуль содержит:
1. Базовые типы-алиасы для часто используемых конструкций
2. Константы для валидации (диапазоны, лимиты)
3. Общие перечисления
4. Утилитарные типы для API

Используйте эти типы везде вместо прямого использования typing.*
"""

from typing import (
    Dict, List, Optional, Union, Any, 
    TypeVar, Callable, Awaitable, Tuple
)
from pydantic import BaseModel, Field
from enum import Enum

# ============================================================================
# БАЗОВЫЕ ТИПЫ-АЛИАСЫ
# ============================================================================

# JSON-совместимые типы
JsonDict = Dict[str, Any]
JsonList = List[Any]
JsonValue = Union[str, int, float, bool, None, JsonDict, JsonList]

# API типы
ApiResponse = JsonDict
ApiRequest = JsonDict
ApiError = JsonDict

# Специализированные словари
StringDict = Dict[str, str]
IntDict = Dict[str, int]
FloatDict = Dict[str, float]
BoolDict = Dict[str, bool]

# Списки
StringList = List[str]
IntList = List[int]
FloatList = List[float]
BoolList = List[bool]

# Опциональные типы
OptionalStr = Optional[str]
OptionalInt = Optional[int]
OptionalFloat = Optional[float]
OptionalBool = Optional[bool]
OptionalDict = Optional[JsonDict]

# ============================================================================
# КОНСТАНТЫ ВАЛИДАЦИИ
# ============================================================================

class VelocityLimits:
    """Ограничения скорости для всех устройств."""
    MIN_PERCENT = 1.0
    MAX_PERCENT = 100.0
    DEFAULT_PERCENT = 50.0

class PositionLimits:
    """Ограничения позиций для Igus."""
    MIN_CM = 0.0
    MAX_CM = 120.0
    DEFAULT_CM = 50.0

class ToolOffsetLimits:
    """Ограничения смещений инструмента xArm."""
    MIN_MM = -1000.0
    MAX_MM = 1000.0
    DEFAULT_MM = 0.0

class JointLimits:
    """Ограничения углов суставов xArm."""
    MIN_DEG = -500.0
    MAX_DEG = 500.0
    DEFAULT_DEG = 0.0

class CoordinateLimits:
    """Ограничения координат AGV."""
    MIN_M = -1000.0
    MAX_M = 1000.0
    DEFAULT_M = 0.0

# ============================================================================
# ПЕРЕЧИСЛЕНИЯ
# ============================================================================

class TaskStatus(str, Enum):
    """Статусы асинхронных задач."""
    PENDING = "pending"
    WORKING = "working"
    FINISHED = "finished"
    ERROR = "error"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"

class DeviceState(str, Enum):
    """Состояния устройств."""
    OFFLINE = "offline"
    ONLINE = "online"
    ERROR = "error"
    BUSY = "busy"
    READY = "ready"

class MovementType(str, Enum):
    """Типы движения."""
    LINEAR = "linear"
    JOINT = "joint"
    TOOL = "tool"
    POSE = "pose"

class ErrorLevel(str, Enum):
    """Уровни ошибок."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

# ============================================================================
# БАЗОВЫЕ МОДЕЛИ
# ============================================================================

class BaseApiModel(BaseModel):
    """Базовая модель для всех API моделей."""
    class Config:
        # Использовать алиасы полей
        validate_by_name = True
        # Строгая валидация
        validate_assignment = True
        # Дополнительные поля запрещены
        extra = "forbid"
        # Разрешить произвольные типы
        arbitrary_types_allowed = True

class BaseResponse(BaseApiModel):
    """Базовая модель для всех ответов API."""
    success: bool = Field(..., description="Успешность операции")
    message: OptionalStr = Field(None, description="Сообщение о результате")

class BaseError(BaseApiModel):
    """Базовая модель для ошибок."""
    error: str = Field(..., description="Текст ошибки")
    code: OptionalInt = Field(None, description="Код ошибки")
    details: OptionalDict = Field(None, description="Дополнительные детали")

class BaseAsyncResponse(BaseApiModel):
    """Базовая модель для асинхронных ответов."""
    success: bool = Field(..., description="Успешность запуска задачи")
    task_id: str = Field(..., description="Идентификатор задачи")

# ============================================================================
# УТИЛИТАРНЫЕ ТИПЫ
# ============================================================================

# Типы для функций
AsyncFunction = TypeVar('AsyncFunction', bound=Callable[..., Awaitable[Any]])
SyncFunction = TypeVar('SyncFunction', bound=Callable[..., Any])

# Типы для координат
Coordinate2D = Tuple[float, float]
Coordinate3D = Tuple[float, float, float]

# Типы для результатов
ResultT = TypeVar('ResultT')
ErrorT = TypeVar('ErrorT')
ResultOrError = Union[ResultT, ErrorT]

# ============================================================================
# СПЕЦИАЛИЗИРОВАННЫЕ ТИПЫ
# ============================================================================



class VelocityPercent(float):
    """Тип для скорости в процентах с валидацией."""
    def __new__(cls, value: float) -> 'VelocityPercent':
        if not VelocityLimits.MIN_PERCENT <= value <= VelocityLimits.MAX_PERCENT:
            raise ValueError(f"Velocity must be between {VelocityLimits.MIN_PERCENT} and {VelocityLimits.MAX_PERCENT}")
        return super().__new__(cls, value)

class PositionCm(float):
    """Тип для позиции в сантиметрах с валидацией."""
    def __new__(cls, value: float) -> 'PositionCm':
        if not PositionLimits.MIN_CM <= value <= PositionLimits.MAX_CM:
            raise ValueError(f"Position must be between {PositionLimits.MIN_CM} and {PositionLimits.MAX_CM}")
        return super().__new__(cls, value)

class ToolOffsetMm(float):
    """Тип для смещения инструмента в миллиметрах с валидацией."""
    def __new__(cls, value: float) -> 'ToolOffsetMm':
        if not ToolOffsetLimits.MIN_MM <= value <= ToolOffsetLimits.MAX_MM:
            raise ValueError(f"Tool offset must be between {ToolOffsetLimits.MIN_MM} and {ToolOffsetLimits.MAX_MM}")
        return super().__new__(cls, value)

class JointAngleDeg(float):
    """Тип для угла сустава в градусах с валидацией."""
    def __new__(cls, value: float) -> 'JointAngleDeg':
        if not JointLimits.MIN_DEG <= value <= JointLimits.MAX_DEG:
            raise ValueError(f"Joint angle must be between {JointLimits.MIN_DEG} and {JointLimits.MAX_DEG}")
        return super().__new__(cls, value)

# ============================================================================
# ЭКСПОРТ
# ============================================================================

__all__ = [
    # Базовые типы
    'JsonDict', 'JsonList', 'JsonValue',
    'ApiResponse', 'ApiRequest', 'ApiError',
    'StringDict', 'IntDict', 'FloatDict', 'BoolDict',
    'StringList', 'IntList', 'FloatList', 'BoolList',
    'OptionalStr', 'OptionalInt', 'OptionalFloat', 'OptionalBool', 'OptionalDict',
    
    # Константы
    'VelocityLimits', 'PositionLimits', 'ToolOffsetLimits', 'JointLimits', 'CoordinateLimits',
    
    # Перечисления
    'TaskStatus', 'DeviceState', 'MovementType', 'ErrorLevel',
    
    # Базовые модели
    'BaseApiModel', 'BaseResponse', 'BaseError', 'BaseAsyncResponse',
    
    # Утилитарные типы
    'AsyncFunction', 'SyncFunction', 'Coordinate2D', 'Coordinate3D', 'ResultOrError',
    
    # Специализированные типы
    'VelocityPercent', 'PositionCm', 'ToolOffsetMm', 'JointAngleDeg',
] 