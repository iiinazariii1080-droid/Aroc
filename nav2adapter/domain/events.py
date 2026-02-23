"""
AE.HUB event models: ack/state/result.

These are used for MQTT publication and HTTP SSE streaming.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Literal, Union

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AckType(str, Enum):
    RECEIVED = "ack.received"
    ACCEPTED = "ack.accepted"


class StateType(str, Enum):
    EXECUTING = "state.executing"
    PROGRESS = "state.progress"


class ResultType(str, Enum):
    SUCCESS = "result.success"
    ERROR = "result.error"
    CANCELED = "result.canceled"


class BaseEvent(BaseModel):
    type: str = Field(..., description="Event type, e.g. ack.received/state.executing/result.success")
    command_id: str = Field(..., description="UUIDv4")
    timestamp: str = Field(default_factory=now_iso, description="ISO8601 timestamp")


class AckEvent(BaseEvent):
    type: Literal["ack.received", "ack.accepted"]


class StateExecutingEvent(BaseEvent):
    type: Literal["state.executing"]


class StateProgressEvent(BaseEvent):
    type: Literal["state.progress"]
    progress_percent: int = Field(default=0, ge=0, le=100)
    eta_seconds: Optional[float] = None


class ResultSuccessEvent(BaseEvent):
    type: Literal["result.success"]


class ResultCanceledEvent(BaseEvent):
    type: Literal["result.canceled"]


class ResultErrorEvent(BaseEvent):
    type: Literal["result.error"]
    reason: str = Field(..., description="Machine-derived or policy-derived error reason")


AnyEvent = Union[
    AckEvent,
    StateExecutingEvent,
    StateProgressEvent,
    ResultSuccessEvent,
    ResultCanceledEvent,
    ResultErrorEvent,
]

