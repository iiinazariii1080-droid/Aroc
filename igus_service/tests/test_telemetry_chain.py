"""IT-03: Telemetry Chain End-to-End (Snapshot → EventBus → SSE).

Verifies that a telemetry snapshot processed through _TelemetryEventProcessor
reaches EventBus subscribers with correct payload fields.

Boundary: E (telemetry callback → EventBus → SSE)
Risk: R-05 — no integration test of full telemetry chain
"""

from __future__ import annotations

import time

import pytest

from app.events import DROPPED, EventBus, EventType
from app.state import _TelemetryEventProcessor
from tests.fakes import FakeSnapshot


@pytest.fixture
def chain():
    """Set up a real EventBus + TelemetryEventProcessor + subscriber."""
    class FakeApp:
        class state:
            drive_last_telemetry_monotonic = 0.0
            drive_fault_active = False
            drive_telemetry_callback_errors_total = 0

    bus = EventBus()
    queue = bus.subscribe()
    processor = _TelemetryEventProcessor(
        app=FakeApp(),  # type: ignore[arg-type]
        event_bus=bus,
        throttle_s=0.0,  # no throttle for tests
    )
    return processor, bus, queue, FakeApp


class TestTelemetryChainDelivery:
    """Snapshot → processor.handle() → EventBus → subscriber queue."""

    def test_status_event_contains_position_and_velocity(self, chain):
        processor, bus, queue, app = chain

        snap = FakeSnapshot(
            statusword=0x0627,
            position=42000,
            velocity=1500,
            mode_display=1,
        )
        processor.handle(
            cia_state=snap.cia402_state,
            fault=False,
            now_monotonic=time.monotonic(),
            snapshot=snap,
        )

        event = queue.get_nowait()
        assert event is not DROPPED
        assert event.type == EventType.STATUS
        assert event.payload["position"] == 42000
        assert event.payload["velocity"] == 1500
        assert event.payload["statusword"] == 0x0627
        assert isinstance(event.payload["cia402_state"], str)
        assert isinstance(event.seq, int) and event.seq > 0
        assert isinstance(event.ts, int) and event.ts > 0

    def test_state_change_event_emitted_on_transition(self, chain):
        processor, bus, queue, app = chain
        now = time.monotonic()

        # First call sets initial state (no event emitted)
        snap1 = FakeSnapshot(statusword=0x0627)
        processor.handle(snap1.cia402_state, False, now, snap1)
        # Drain STATUS event
        queue.get_nowait()

        # Second call with different state → STATE_CHANGE event
        snap2 = FakeSnapshot(statusword=0x0240)  # SWITCH_ON_DISABLED
        processor.handle(snap2.cia402_state, False, now + 0.1, snap2)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        state_changes = [e for e in events if e is not DROPPED and e.type == EventType.STATE_CHANGE]
        assert len(state_changes) >= 1
        sc = state_changes[0]
        assert "from_state" in sc.payload
        assert "to_state" in sc.payload
        assert sc.payload["statusword"] == 0x0240

    def test_fault_event_emitted_on_fault_edge(self, chain):
        processor, bus, queue, app = chain
        now = time.monotonic()

        # Initial: no fault
        snap1 = FakeSnapshot(statusword=0x0627)
        processor.handle(snap1.cia402_state, False, now, snap1)
        queue.get_nowait()  # drain STATUS

        # Fault edge: True
        snap2 = FakeSnapshot(statusword=0x0208)  # FAULT
        processor.handle(snap2.cia402_state, True, now + 0.1, snap2)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        fault_events = [e for e in events if e is not DROPPED and e.type == EventType.FAULT]
        assert len(fault_events) >= 1
        assert fault_events[0].payload["active"] is True

    def test_app_state_updated_by_processor(self, chain):
        processor, bus, queue, app = chain
        now = time.monotonic()

        snap = FakeSnapshot(statusword=0x0627)
        processor.handle(snap.cia402_state, True, now, snap)

        assert app.state.drive_fault_active is True
        assert app.state.drive_last_telemetry_monotonic == now

    def test_callback_error_counter_decay(self, chain):
        processor, bus, queue, app = chain

        # Increment errors
        app.state.drive_telemetry_callback_errors_total = 5

        # Handle snapshot with time far enough for decay (>60s default window)
        snap = FakeSnapshot(statusword=0x0627)
        processor.handle(snap.cia402_state, False, time.monotonic() + 120, snap)

        assert app.state.drive_telemetry_callback_errors_total == 0
