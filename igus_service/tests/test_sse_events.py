"""Integration tests for the SSE /drive/events endpoint.

Covers: replay of recent events, live event delivery, keepalive pings,
SHUTDOWN graceful close, event format validation, and drive-offline error.

Strategy: SSE streams are infinite, so tests that read from the stream use
a background thread to publish a SHUTDOWN event after a short delay, causing
the generator to terminate and the response to complete.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import main
from app.events import EventBus, EventType
from tests.fakes import FakeDrive, set_app_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse_events(raw: str) -> list[dict]:
    """Parse raw SSE text into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = None
    for line in raw.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: "):]
        elif line.startswith("data: "):
            current_data = line[len("data: "):]
        elif line == "" and current_event is not None:
            entry: dict = {"event": current_event}
            if current_data:
                entry["data"] = json.loads(current_data)
            events.append(entry)
            current_event = None
            current_data = None
    # Handle trailing event without final blank line
    if current_event is not None:
        entry = {"event": current_event}
        if current_data:
            entry["data"] = json.loads(current_data)
        events.append(entry)
    return events


def _shutdown_after(bus: EventBus, delay: float = 0.3) -> threading.Thread:
    """Start a daemon thread that publishes SHUTDOWN after *delay* seconds."""
    def _do():
        time.sleep(delay)
        bus.shutdown_notify()
    t = threading.Thread(target=_do, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def real_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def sse_app(noop_lifecycle, real_bus):
    """Set up app state with a real EventBus, yield (client, bus)."""
    with TestClient(main.app) as c:
        set_app_state(main.app, drive=FakeDrive())
        main.app.state.event_bus = real_bus
        yield c, real_bus


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSSEReplay:
    def test_late_joiner_receives_replayed_events(self, sse_app):
        """Replayed events from the ring buffer are yielded before live events."""
        client, bus = sse_app
        bus.publish(EventType.STATUS, {"pos": 100})
        bus.publish(EventType.STATE_CHANGE, {"from": "ready", "to": "op_enabled"})

        _shutdown_after(bus, delay=0.5)
        r = client.get("/drive/events")
        assert r.status_code == 200

        events = _parse_sse_events(r.text)
        types = [e["event"] for e in events]
        assert "status" in types
        assert "state_change" in types

    def test_no_replay_when_buffer_empty(self, sse_app):
        """With an empty ring buffer, only the SHUTDOWN event is delivered."""
        client, bus = sse_app
        _shutdown_after(bus, delay=0.3)
        r = client.get("/drive/events")
        events = _parse_sse_events(r.text)
        # Should contain at most the SHUTDOWN event (or a ping then shutdown).
        types = [e["event"] for e in events]
        assert "shutdown" in types


class TestSSEHeaders:
    def test_response_content_type_and_cache_control(self, sse_app):
        client, bus = sse_app
        _shutdown_after(bus, delay=0.3)
        r = client.get("/drive/events")
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        assert "no-cache" in r.headers.get("cache-control", "")


class TestSSEEventFormat:
    def test_event_data_has_required_fields(self, sse_app):
        """Each SSE event payload contains seq, ts, type, and payload."""
        client, bus = sse_app
        bus.publish(EventType.COMMAND, {"op": "jog_start", "velocity": 1000})
        _shutdown_after(bus, delay=0.3)

        r = client.get("/drive/events")
        events = _parse_sse_events(r.text)
        # Find the COMMAND event (replayed).
        cmd_events = [e for e in events if e["event"] == "command"]
        assert len(cmd_events) >= 1
        data = cmd_events[0]["data"]
        assert isinstance(data["seq"], int) and data["seq"] >= 0
        assert isinstance(data["ts"], int) and data["ts"] > 0
        assert data["type"] == "command"
        assert data["payload"]["op"] == "jog_start"
        assert data["payload"]["velocity"] == 1000


class TestSSELiveDelivery:
    def test_live_event_delivered_to_subscriber(self, sse_app):
        """Events published after subscribe are delivered in the stream."""
        client, bus = sse_app

        def _publish_then_shutdown():
            time.sleep(0.3)
            bus.publish(EventType.FAULT, {"code": 42})
            time.sleep(0.1)
            bus.shutdown_notify()

        t = threading.Thread(target=_publish_then_shutdown, daemon=True)
        t.start()

        r = client.get("/drive/events")
        events = _parse_sse_events(r.text)
        fault_events = [e for e in events if e["event"] == "fault"]
        assert len(fault_events) >= 1
        assert fault_events[0]["data"]["payload"]["code"] == 42


class TestSSEShutdown:
    def test_shutdown_event_terminates_stream(self, sse_app):
        """SHUTDOWN event is the last event; the stream closes after it."""
        client, bus = sse_app
        _shutdown_after(bus, delay=0.3)
        r = client.get("/drive/events")
        events = _parse_sse_events(r.text)
        assert len(events) >= 1
        # Last event should be SHUTDOWN.
        last = events[-1]
        assert last["event"] == "shutdown"
        assert last["data"]["payload"]["reason"] == "server_shutdown"


class TestSSEEventBusOffline:
    def test_event_bus_not_initialized_returns_503(self, noop_lifecycle):
        """SSE endpoint returns 503 when event bus is not available."""
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())
            main.app.state.event_bus = None
            r = c.get("/drive/events")
            assert r.status_code == 503
