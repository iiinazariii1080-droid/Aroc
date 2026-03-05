"""Tests for app/navigation_facade.py — NavigationFacade."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.navigation_facade import NavigationFacade, CommandSendResult
from services.mqtt_adapter import MqttUnavailableError


_SETTINGS_PATCH = {
    "mqtt_command_retry_wait_s": 0,    # disable wait by default in tests (fast)
    "mqtt_command_retry_poll_s": 0.05,
}


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


def _patch_settings(**extra):
    merged = {**_SETTINGS_PATCH, **extra}
    return patch("app.navigation_facade.settings", **merged)


# ── send_navigate_to ────────────────────────────────────────────
class TestSendNavigateTo:
    @pytest.mark.asyncio
    async def test_mqtt_happy_path(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            result = await facade.send_navigate_to(target_id="station_A")
        assert result.delivery == "mqtt"
        mock_mqtt.publish_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mqtt_error_wraps_as_unavailable(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=OSError("conn lost"))
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError):
                await facade.send_navigate_to(target_id="station_A")

    @pytest.mark.asyncio
    async def test_mqtt_generic_error_wraps(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=ValueError("unexpected"))
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError, match="publish_failed"):
                await facade.send_navigate_to(target_id="station_A")

    @pytest.mark.asyncio
    async def test_local_fallback(self, mock_handler):
        facade = NavigationFacade(
            None, command_handler=mock_handler, allow_direct_http_commands=True
        )
        with _patch_settings():
            result = await facade.send_navigate_to(target_id="station_B")
        assert result.delivery == "local"
        mock_handler.handle_navigate_to.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_mqtt_no_local_raises(self):
        facade = NavigationFacade(None)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError, match="not connected"):
                await facade.send_navigate_to(target_id="X")

    @pytest.mark.asyncio
    async def test_custom_command_id(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            result = await facade.send_navigate_to(
                target_id="dock", command_id="custom-id", timestamp="2024-01-01T00:00:00Z"
            )
        assert result.payload["command_id"] == "custom-id"

    @pytest.mark.asyncio
    async def test_mqtt_disconnected_falls_through(self, mock_mqtt, mock_handler):
        """With wait=0, disconnected MQTT falls through to local immediately."""
        mock_mqtt.is_connected = False
        facade = NavigationFacade(
            mock_mqtt, command_handler=mock_handler, allow_direct_http_commands=True
        )
        with _patch_settings(mqtt_command_retry_wait_s=0):
            result = await facade.send_navigate_to(target_id="station_C")
        assert result.delivery == "local"


# ── send_cancel ─────────────────────────────────────────────────
class TestSendCancel:
    @pytest.mark.asyncio
    async def test_mqtt_cancel(self, mock_mqtt):
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            result = await facade.send_cancel(command_id="cmd-1")
        assert result.delivery == "mqtt"
        mock_mqtt.publish_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_mqtt_error(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=OSError("fail"))
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError):
                await facade.send_cancel(command_id="cmd-1")

    @pytest.mark.asyncio
    async def test_cancel_generic_error(self, mock_mqtt):
        mock_mqtt.publish_command = AsyncMock(side_effect=RuntimeError("bad"))
        facade = NavigationFacade(mock_mqtt)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError, match="publish_failed"):
                await facade.send_cancel(command_id="cmd-1")

    @pytest.mark.asyncio
    async def test_cancel_local(self, mock_handler):
        facade = NavigationFacade(
            None, command_handler=mock_handler, allow_direct_http_commands=True
        )
        with _patch_settings():
            result = await facade.send_cancel(command_id="cmd-2")
        assert result.delivery == "local"
        mock_handler.handle_cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_no_adapter(self):
        facade = NavigationFacade(None)
        with _patch_settings():
            with pytest.raises(MqttUnavailableError):
                await facade.send_cancel(command_id="cmd-3")


# ── MQTT reconnect wait ────────────────────────────────────────
class TestMqttReconnectWait:
    """NavigationFacade waits for MQTT to reconnect before failing with 503."""

    @pytest.mark.asyncio
    async def test_wait_reconnects_then_publishes(self, mock_mqtt):
        """MQTT disconnected initially, reconnects after 0.2s → publish succeeds."""
        mock_mqtt.is_connected = False

        # Simulate reconnect: after 2 polls is_connected becomes True
        call_count = 0
        original_is_connected = False

        def _is_connected_side_effect():
            nonlocal call_count, original_is_connected
            call_count += 1
            if call_count >= 3:
                original_is_connected = True
            return original_is_connected

        type(mock_mqtt).is_connected = property(lambda self: _is_connected_side_effect())

        facade = NavigationFacade(mock_mqtt)
        with _patch_settings(mqtt_command_retry_wait_s=2.0, mqtt_command_retry_poll_s=0.1):
            result = await facade.send_navigate_to(target_id="pos_A")

        assert result.delivery == "mqtt"
        mock_mqtt.publish_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wait_timeout_no_local_raises(self, mock_mqtt):
        """MQTT never reconnects, no local fallback → MqttUnavailableError."""
        mock_mqtt.is_connected = False
        facade = NavigationFacade(mock_mqtt)

        with _patch_settings(mqtt_command_retry_wait_s=0.3, mqtt_command_retry_poll_s=0.1):
            with pytest.raises(MqttUnavailableError):
                await facade.send_navigate_to(target_id="X")

    @pytest.mark.asyncio
    async def test_wait_timeout_falls_to_local(self, mock_mqtt, mock_handler):
        """MQTT never reconnects but local handler exists → local delivery."""
        mock_mqtt.is_connected = False
        facade = NavigationFacade(
            mock_mqtt, command_handler=mock_handler, allow_direct_http_commands=True
        )

        with _patch_settings(mqtt_command_retry_wait_s=0.3, mqtt_command_retry_poll_s=0.1):
            result = await facade.send_navigate_to(target_id="pos_B")

        assert result.delivery == "local"

    @pytest.mark.asyncio
    async def test_wait_zero_disables(self, mock_mqtt):
        """mqtt_command_retry_wait_s=0 → no wait, immediate failure."""
        mock_mqtt.is_connected = False
        facade = NavigationFacade(mock_mqtt)

        with _patch_settings(mqtt_command_retry_wait_s=0):
            with pytest.raises(MqttUnavailableError):
                await facade.send_navigate_to(target_id="X")

    @pytest.mark.asyncio
    async def test_cancel_also_waits(self, mock_mqtt):
        """send_cancel also benefits from reconnect wait."""
        mock_mqtt.is_connected = False

        call_count = 0

        def _is_connected():
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        type(mock_mqtt).is_connected = property(lambda self: _is_connected())

        facade = NavigationFacade(mock_mqtt)
        with _patch_settings(mqtt_command_retry_wait_s=2.0, mqtt_command_retry_poll_s=0.1):
            result = await facade.send_cancel(command_id="cmd-wait")

        assert result.delivery == "mqtt"
