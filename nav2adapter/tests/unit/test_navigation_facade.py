"""Tests for app/navigation_facade.py — NavigationFacade."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.navigation_facade import NavigationFacade, CommandSendResult
from services.mqtt_adapter import MqttUnavailableError


@pytest.fixture
def mock_mqtt():
    m = MagicMock()
    m.is_connected = True
    m.publish_command = AsyncMock()
    return m


@pytest.fixture
def mock_handler():
    m = MagicMock()
    m.handle_navigate_to = AsyncMock()
    m.handle_cancel = AsyncMock()
    return m


# ── send_navigate_to ────────────────────────────────────────────
class TestSendNavigateTo:
    @pytest.mark.asyncio
    async def test_mqtt_happy_path(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        result = await facade.send_navigate_to(target_id="station_A")
        assert result.delivery == "mqtt"
        mock_mqtt.publish_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mqtt_error_wraps_as_unavailable(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=OSError("conn lost"))
        facade = NavigationFacade(mock_mqtt)
        with pytest.raises(MqttUnavailableError):
            await facade.send_navigate_to(target_id="station_A")

    @pytest.mark.asyncio
    async def test_mqtt_generic_error_wraps(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=ValueError("unexpected"))
        facade = NavigationFacade(mock_mqtt)
        with pytest.raises(MqttUnavailableError, match="publish_failed"):
            await facade.send_navigate_to(target_id="station_A")

    @pytest.mark.asyncio
    async def test_local_fallback(self, mock_handler):
        facade = NavigationFacade(
            None, command_handler=mock_handler, allow_direct_http_commands=True
        )
        result = await facade.send_navigate_to(target_id="station_B")
        assert result.delivery == "local"
        mock_handler.handle_navigate_to.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_mqtt_no_local_raises(self):
        facade = NavigationFacade(None)
        with pytest.raises(MqttUnavailableError, match="not connected"):
            await facade.send_navigate_to(target_id="X")

    @pytest.mark.asyncio
    async def test_custom_command_id(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        result = await facade.send_navigate_to(
            target_id="dock", command_id="custom-id", timestamp="2024-01-01T00:00:00Z"
        )
        assert result.payload["command_id"] == "custom-id"

    @pytest.mark.asyncio
    async def test_mqtt_disconnected_falls_through(self, mock_mqtt, mock_handler):
        mock_mqtt.is_connected = False
        facade = NavigationFacade(
            mock_mqtt, command_handler=mock_handler, allow_direct_http_commands=True
        )
        result = await facade.send_navigate_to(target_id="station_C")
        assert result.delivery == "local"


# ── send_cancel ─────────────────────────────────────────────────
class TestSendCancel:
    @pytest.mark.asyncio
    async def test_mqtt_cancel(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        result = await facade.send_cancel(command_id="cmd-1")
        assert result.delivery == "mqtt"
        mock_mqtt.publish_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_mqtt_error(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=OSError("fail"))
        facade = NavigationFacade(mock_mqtt)
        with pytest.raises(MqttUnavailableError):
            await facade.send_cancel(command_id="cmd-1")

    @pytest.mark.asyncio
    async def test_cancel_generic_error(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=RuntimeError("bad"))
        facade = NavigationFacade(mock_mqtt)
        with pytest.raises(MqttUnavailableError, match="publish_failed"):
            await facade.send_cancel(command_id="cmd-1")

    @pytest.mark.asyncio
    async def test_cancel_local(self, mock_handler):
        facade = NavigationFacade(
            None, command_handler=mock_handler, allow_direct_http_commands=True
        )
        result = await facade.send_cancel(command_id="cmd-2")
        assert result.delivery == "local"
        mock_handler.handle_cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_no_adapter(self):
        facade = NavigationFacade(None)
        with pytest.raises(MqttUnavailableError):
            await facade.send_cancel(command_id="cmd-3")
