"""Tests for undertested helper modules: cache, events, service_error_http, decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.cache import StateCache
from app.events import EventBus, EventType
from app.service_error_http import raise_service_error_http

# ---------------------------------------------------------------------------
# StateCache
# ---------------------------------------------------------------------------


@dataclass
class _FakeSnapshot:
    statusword: int = 0
    cia402_state: str = "SWITCH_ON_DISABLED"
    position: int = 100
    velocity: int = 0
    mode_display: int = 1
    decoded_status: dict[str, bool] | None = None
    ts_monotonic_s: float = 1.0


class _FakeDriveWithTelemetry:
    def __init__(self, snapshot: _FakeSnapshot | None = None) -> None:
        self._snapshot = snapshot

    def telemetry_latest(self) -> _FakeSnapshot | None:
        return self._snapshot

    def telemetry_poll_info(self) -> dict[str, Any]:
        return {"is_running": True, "interval_s": 0.1}


def test_state_cache_returns_none_when_no_drive() -> None:
    cache = StateCache(None)
    assert cache.get_cached_status() is None


def test_state_cache_returns_none_when_no_snapshot() -> None:
    drive = _FakeDriveWithTelemetry(snapshot=None)
    cache = StateCache(drive)
    assert cache.get_cached_status() is None


def test_state_cache_returns_snapshot_dict() -> None:
    snap = _FakeSnapshot(statusword=42, position=9999)
    drive = _FakeDriveWithTelemetry(snapshot=snap)
    cache = StateCache(drive)

    result = cache.get_cached_status()
    assert result is not None
    assert result["statusword"] == 42
    assert result["position"] == 9999
    assert "cia402_state" in result


def test_state_cache_poll_info_from_drive() -> None:
    drive = _FakeDriveWithTelemetry()
    cache = StateCache(drive)
    info = cache.get_poll_info()
    assert info["is_running"] is True
    assert info["interval_s"] == 0.1


def test_state_cache_poll_info_no_drive() -> None:
    cache = StateCache(None)
    info = cache.get_poll_info()
    assert info["is_running"] is False
    assert info["interval_s"] is None


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


async def test_event_bus_publish_and_subscribe() -> None:
    bus = EventBus(max_queue_size=10)
    sub = bus.subscribe()

    await bus.publish(EventType.STATUS, {"key": "value"})

    event = sub.get_nowait()
    assert event.type == EventType.STATUS
    assert event.payload["key"] == "value"
    assert event.seq == 1


async def test_event_bus_unsubscribe() -> None:
    bus = EventBus(max_queue_size=10)
    sub = bus.subscribe()
    bus.unsubscribe(sub)

    await bus.publish(EventType.FAULT, {"active": True})

    assert sub.empty()


async def test_event_bus_drops_oldest_on_full() -> None:
    bus = EventBus(max_queue_size=2)

    await bus.publish(EventType.STATUS, {"n": 1})
    await bus.publish(EventType.STATUS, {"n": 2})
    await bus.publish(EventType.STATUS, {"n": 3})

    events = await bus.get_recent_events(limit=10)
    # Should have dropped oldest, kept 2 most recent
    assert len(events) == 2
    payloads = [e.payload["n"] for e in events]
    assert 3 in payloads


async def test_event_bus_get_recent_events_preserves_queue() -> None:
    bus = EventBus(max_queue_size=10)
    await bus.publish(EventType.COMMAND, {"op": "move"})
    await bus.publish(EventType.COMMAND, {"op": "stop"})

    events1 = await bus.get_recent_events()
    events2 = await bus.get_recent_events()
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
        return {"error": self.message, "code": self.code}


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
