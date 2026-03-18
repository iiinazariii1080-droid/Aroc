import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.events import AckEvent, AckType, ResultSuccessEvent, ResultType
from services.event_bus import EventBus
from services.event_dispatcher import EventDispatcher


@pytest.mark.asyncio
async def test_dispatcher_consume_ok_counter() -> None:
    """Consuming an event increments consume.ok counter."""
    bus = EventBus(queue_size=10)
    dispatcher = EventDispatcher(bus)

    with patch("services.event_dispatcher.reliability_metrics") as metrics:
        await dispatcher.start()
        await asyncio.sleep(0.02)
        await bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id="cmd-1"))
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        metrics.inc.assert_any_call("event_dispatcher.consume.ok")


@pytest.mark.asyncio
async def test_dispatcher_persist_failure_counted() -> None:
    """If persist raises, consume.failed is incremented."""
    bus = EventBus(queue_size=10)
    ss = MagicMock()
    ss.set_last_result = AsyncMock(side_effect=Exception("persist-failed"))
    dispatcher = EventDispatcher(bus, state_store=ss)

    with patch("services.event_dispatcher.reliability_metrics") as metrics:
        await dispatcher.start()
        await asyncio.sleep(0.02)
        await bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id="cmd-3"))
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        metrics.inc.assert_any_call("event_dispatcher.consume.failed")
