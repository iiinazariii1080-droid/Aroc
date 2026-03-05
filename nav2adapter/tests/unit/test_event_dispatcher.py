"""Tests for services/event_dispatcher.py — start/stop, event processing, MQTT publish."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.event_dispatcher import EventDispatcher
from services.event_bus import EventBus
from domain.events import AckEvent, ResultSuccessEvent, StateProgressEvent


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus, MagicMock())
        await d.start()
        assert d._running is True
        assert d._task is not None
        await d.stop()
        assert d._running is False
        assert d._task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus, MagicMock())
        await d.start()
        task1 = d._task
        await d.start()  # no-op
        assert d._task is task1
        await d.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus, MagicMock())
        await d.stop()  # no-op, should not crash


class TestEventProcessing:
    @pytest.mark.asyncio
    async def test_publishes_ack_to_mqtt(self):
        bus = EventBus(queue_size=10)
        mqtt = MagicMock()
        mqtt.is_connected = True
        mqtt.publish_event = AsyncMock()
        d = EventDispatcher(bus, mqtt)
        await d.start()
        await asyncio.sleep(0.05)  # let task subscribe
        try:
            event = AckEvent(type="ack.received", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            mqtt.publish_event.assert_called()
            kind_arg = mqtt.publish_event.call_args[0][0]
            assert kind_arg == "ack"
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_result_event_persists(self):
        bus = EventBus(queue_size=10)
        mqtt = MagicMock()
        mqtt.is_connected = True
        mqtt.publish_event = AsyncMock()
        d = EventDispatcher(bus, mqtt)
        with patch("services.event_dispatcher.state_store") as ss:
            ss.set_last_result = AsyncMock()
            await d.start()
            await asyncio.sleep(0.05)  # let task subscribe
            try:
                event = ResultSuccessEvent(type="result.success", command_id="c1")
                await bus.publish(event)
                await asyncio.sleep(0.2)
                ss.set_last_result.assert_called_once()
            finally:
                await d.stop()

    @pytest.mark.asyncio
    async def test_skips_mqtt_when_disconnected(self):
        bus = EventBus(queue_size=10)
        mqtt = MagicMock()
        mqtt.is_connected = False
        mqtt.publish_event = AsyncMock()
        d = EventDispatcher(bus, mqtt)
        await d.start()
        await asyncio.sleep(0.05)  # let task subscribe
        try:
            event = AckEvent(type="ack.received", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            mqtt.publish_event.assert_not_called()
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_no_mqtt_at_all(self):
        bus = EventBus(queue_size=10)
        d = EventDispatcher(bus, None)
        await d.start()
        await asyncio.sleep(0.05)  # let task subscribe
        try:
            event = AckEvent(type="ack.received", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            # Should process without crash
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_error_in_publish_does_not_crash(self):
        bus = EventBus(queue_size=10)
        mqtt = MagicMock()
        mqtt.is_connected = True
        mqtt.publish_event = AsyncMock(side_effect=RuntimeError("boom"))
        d = EventDispatcher(bus, mqtt)
        await d.start()
        await asyncio.sleep(0.05)  # let task subscribe
        try:
            event = AckEvent(type="ack.received", command_id="c1")
            await bus.publish(event)
            await asyncio.sleep(0.2)
            # Should still be running
            assert d._running is True
        finally:
            await d.stop()


class TestKindFromType:
    def test_ack(self):
        d = EventDispatcher.__new__(EventDispatcher)
        assert d._kind_from_type("ack.received") == "ack"
        assert d._kind_from_type("ack.accepted") == "ack"

    def test_state(self):
        d = EventDispatcher.__new__(EventDispatcher)
        assert d._kind_from_type("state.executing") == "state"
        assert d._kind_from_type("state.progress") == "state"

    def test_result(self):
        d = EventDispatcher.__new__(EventDispatcher)
        assert d._kind_from_type("result.success") == "result"
        assert d._kind_from_type("result.canceled") == "result"

    def test_unknown(self):
        d = EventDispatcher.__new__(EventDispatcher)
        assert d._kind_from_type("something.else") == "unknown"
