"""Typed payload models for MQTT responses.

Replaces ad-hoc dict construction throughout bridge.py with
structured, validated dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.constants import ErrorType, MessageType, StatusType


@dataclass
class ErrorDetail:
    type: str
    message: str
    extra: dict[str, Any] | None = None

    @classmethod
    def http_error(cls, message: str, **extra: Any) -> ErrorDetail:
        return cls(type=ErrorType.HTTP_ERROR.value, message=message, extra=extra or None)

    @classmethod
    def command_error(cls, message: str, **extra: Any) -> ErrorDetail:
        return cls(type=ErrorType.COMMAND_ERROR.value, message=message, extra=extra or None)

    @classmethod
    def routing_error(cls, message: str, **extra: Any) -> ErrorDetail:
        return cls(type=ErrorType.ROUTING_ERROR.value, message=message, extra=extra or None)

    @classmethod
    def invalid_json(cls, message: str, **extra: Any) -> ErrorDetail:
        return cls(type=ErrorType.INVALID_JSON.value, message=message, extra=extra or None)

    @classmethod
    def processing_error(cls, message: str, **extra: Any) -> ErrorDetail:
        return cls(type=ErrorType.PROCESSING_ERROR.value, message=message, extra=extra or None)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "message": self.message}
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class AckPayload:
    """Acknowledgement response for a command or service request."""

    request_id: str | None
    service: str
    success: bool
    status_code: int
    body: Any = None
    error: ErrorDetail | None = None
    command_id: str | None = None
    command_name: str | None = None
    task_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": MessageType.ACK.value,
            "request_id": self.request_id,
            "service": self.service,
            "success": self.success,
            "status_code": self.status_code,
            "body": self.body,
        }
        if self.error is not None:
            d["error"] = self.error.to_dict()
        if self.command_id is not None:
            d["command_id"] = self.command_id
        if self.command_name is not None:
            d["command_name"] = self.command_name
        if self.task_id is not None:
            d["task_id"] = self.task_id
        if self.headers:
            d["headers"] = self.headers
        return d


@dataclass(frozen=True)
class CommandContext:
    """Typed context passed through the command processing pipeline.

    Replaces the untyped ``dict[str, Any]`` that was previously used
    to carry command metadata between handlers.
    """

    command_name: str
    command_id: str
    status_type: str = StatusType.NAVIGATION.value
    publish_navigation: bool = True
    target_id: str | None = None
    task_id: str | None = None


@dataclass
class ResultPayload:
    """Task completion result payload."""

    request_id: str
    task_id: str
    service: str
    success: bool
    status_code: int | None
    body: Any = None
    error: ErrorDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": MessageType.RESULT.value,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "service": self.service,
            "success": self.success,
            "status_code": self.status_code,
            "body": self.body,
        }
        if self.error is not None:
            d["error"] = self.error.to_dict()
        return d
