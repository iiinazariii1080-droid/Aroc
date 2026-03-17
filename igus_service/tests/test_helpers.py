"""Tests for undertested helper modules: events, service_error_http, decorator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.events import EventBus, EventType
from app.service_error_http import raise_service_error_http

# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


async def test_event_bus_publish_and_subscribe() -> None:
    bus = EventBus(recent_buffer_size=10)
    sub = bus.subscribe()

    bus.publish(EventType.STATUS, {"key": "value"})

    event = sub.get_nowait()
    assert event.type == EventType.STATUS
    assert event.payload["key"] == "value"
    assert event.seq == 1


async def test_event_bus_unsubscribe() -> None:
    bus = EventBus(recent_buffer_size=10)
    sub = bus.subscribe()
    bus.unsubscribe(sub)

    bus.publish(EventType.FAULT, {"active": True})

    assert sub.empty()


async def test_event_bus_drops_oldest_on_full() -> None:
    bus = EventBus(recent_buffer_size=2)

    bus.publish(EventType.STATUS, {"n": 1})
    bus.publish(EventType.STATUS, {"n": 2})
    bus.publish(EventType.STATUS, {"n": 3})

    events = bus.get_recent_events(limit=10)
    # Should have dropped oldest, kept 2 most recent
    assert len(events) == 2
    payloads = [e.payload["n"] for e in events]
    assert 3 in payloads


async def test_event_bus_get_recent_events_preserves_queue() -> None:
    bus = EventBus(recent_buffer_size=10)
    bus.publish(EventType.COMMAND, {"op": "move"})
    bus.publish(EventType.COMMAND, {"op": "stop"})

    events1 = bus.get_recent_events()
    events2 = bus.get_recent_events()
    assert len(events1) == len(events2) == 2


# ---------------------------------------------------------------------------
# raise_service_error_http
# ---------------------------------------------------------------------------


class _FakeServiceError:
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message

    def to_error_detail(self) -> dict[str, str]:
        return {"message": self.message, "code": self.code}


def test_raise_service_error_http_raises_http_exception() -> None:
    from app.application.drive_service import ServiceError

    exc = ServiceError(503, "DRIVE_OFFLINE", "Drive not connected")
    with pytest.raises(HTTPException) as exc_info:
        raise_service_error_http(exc)
    assert exc_info.value.status_code == 503


def test_raise_service_error_http_records_metric() -> None:
    from app.application.drive_service import ServiceError

    exc = ServiceError(504, "TIMEOUT", "Timed out")
    metrics = MagicMock()
    request = MagicMock()
    request.app.state.metrics = metrics

    with pytest.raises(HTTPException):
        raise_service_error_http(exc, request=request, operation="move")

    metrics.observe_drive_operation_error.assert_called_once_with(
        operation="move",
        code="TIMEOUT",
        status_code=504,
    )


# ---------------------------------------------------------------------------
# TD-04: Mode display map covers OperationMode enum
# ---------------------------------------------------------------------------


def test_mode_display_map_covers_operation_modes() -> None:
    """_MODE_DISPLAY_MAP values must match non-UNKNOWN OperationMode enum members."""
    from app.api_models import OperationMode
    from app.application.mappers import _MODE_DISPLAY_MAP

    mapped_values = set(_MODE_DISPLAY_MAP.values())
    enum_values = {m.value for m in OperationMode if m.value != "UNKNOWN"}
    assert mapped_values == enum_values, (
        f"Mismatch: mapped={mapped_values}, enum={enum_values}"
    )
