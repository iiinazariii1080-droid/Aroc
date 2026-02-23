import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.events import AckEvent, AckType
from services.event_bus import EventBus
from services.event_dispatcher import EventDispatcher


@pytest.mark.asyncio
async def test_dispatcher_skipped_when_mqtt_disconnected() -> None:
    bus = EventBus(queue_size=10)
    mqtt = MagicMock()
    mqtt.is_connected = False
    mqtt.publish_event = AsyncMock()
    dispatcher = EventDispatcher(bus, mqtt)

    with patch("services.event_dispatcher.reliability_metrics") as metrics:
        await dispatcher.start()
        await asyncio.sleep(0.02)
        await bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id="cmd-1"))
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        metrics.inc.assert_any_call("event_dispatcher.consume.ok")
        metrics.inc.assert_any_call("event_dispatcher.publish.skipped_disconnected")
        mqtt.publish_event.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_publish_success_counter() -> None:
    bus = EventBus(queue_size=10)
    mqtt = MagicMock()
    mqtt.is_connected = True
    mqtt.publish_event = AsyncMock()
    dispatcher = EventDispatcher(bus, mqtt)

    with patch("services.event_dispatcher.reliability_metrics") as metrics:
        await dispatcher.start()
        await asyncio.sleep(0.02)
        await bus.publish(AckEvent(type=AckType.ACCEPTED.value, command_id="cmd-2"))
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        metrics.inc.assert_any_call("event_dispatcher.consume.ok")
        metrics.inc.assert_any_call("event_dispatcher.publish.success")
        mqtt.publish_event.assert_awaited()


@pytest.mark.asyncio
async def test_dispatcher_failure_counters() -> None:
    bus = EventBus(queue_size=10)
    mqtt = MagicMock()
    mqtt.is_connected = True
    mqtt.publish_event = AsyncMock(side_effect=Exception("publish-failed"))
    dispatcher = EventDispatcher(bus, mqtt)

    with patch("services.event_dispatcher.reliability_metrics") as metrics:
        await dispatcher.start()
        await asyncio.sleep(0.02)
        await bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id="cmd-3"))
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        metrics.inc.assert_any_call("event_dispatcher.consume.failed")
        metrics.inc.assert_any_call("event_dispatcher.runtime.failed")
