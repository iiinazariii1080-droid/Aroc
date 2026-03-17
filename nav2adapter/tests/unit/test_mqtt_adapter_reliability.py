import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.mqtt_adapter import MqttAdapter, MqttUnavailableError


def _make_adapter(*, connected: bool) -> MqttAdapter:
    adapter = object.__new__(MqttAdapter)
    adapter.robot_id = "robot-test"
    adapter.client = MagicMock()
    adapter.client.publish = AsyncMock()
    adapter._connected = connected
    adapter._mark_disconnected = MagicMock()
    adapter._get_event_topic = lambda kind: f"aroc/robot/robot-test/events/{kind}"
    return adapter


@pytest.mark.asyncio
async def test_publish_event_disconnected_increments_skipped_counter() -> None:
    adapter = _make_adapter(connected=False)

    with patch("services.mqtt_adapter.reliability_metrics") as metrics:
        await adapter.publish_event("ack", {"x": 1})

        metrics.inc.assert_called_with("mqtt.publish.event.skipped_disconnected")
        adapter.client.publish.assert_not_called()


@pytest.mark.asyncio
async def test_publish_event_success_increments_success_and_observes_latency() -> None:
    adapter = _make_adapter(connected=True)

    with patch("services.mqtt_adapter.reliability_metrics") as metrics:
        await adapter.publish_event("result", {"ok": True})

        metrics.inc.assert_any_call("mqtt.publish.event.success")
        metrics.observe_duration.assert_called()
        adapter.client.publish.assert_called_once()


@pytest.mark.asyncio
async def test_publish_event_failure_increments_failure_and_marks_disconnected() -> None:
    adapter = _make_adapter(connected=True)
    adapter.client.publish = AsyncMock(side_effect=Exception("publish failed"))

    with patch("services.mqtt_adapter.reliability_metrics") as metrics:
        await adapter.publish_event("state", {"v": 1})

        metrics.inc.assert_any_call("mqtt.publish.event.failure")
        adapter._mark_disconnected.assert_called_once()


@pytest.mark.asyncio
async def test_publish_command_disconnected_raises_and_increments_skipped() -> None:
    adapter = _make_adapter(connected=False)

    with patch("services.mqtt_adapter.reliability_metrics") as metrics:
        with pytest.raises(MqttUnavailableError):
            await adapter.publish_command("driveToPosition", {"command_id": "c1"})

        metrics.inc.assert_called_with("mqtt.publish.command.skipped_disconnected")


@pytest.mark.asyncio
async def test_publish_command_success_increments_success_and_observes_latency() -> None:
    adapter = _make_adapter(connected=True)

    with patch("services.mqtt_adapter.reliability_metrics") as metrics:
        await adapter.publish_command("cancel", {"command_id": "c2"})

        metrics.inc.assert_any_call("mqtt.publish.command.success")
        metrics.observe_duration.assert_called()
        adapter.client.publish.assert_called_once()
