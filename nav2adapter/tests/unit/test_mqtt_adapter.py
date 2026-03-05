"""Tests for MqttAdapter — topic building, decode, publish, connect/disconnect, mark_disconnected."""
import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.mqtt_adapter import MqttAdapter, MqttUnavailableError


@pytest.fixture
def adapter():
    """MqttAdapter with patched settings (no real broker)."""
    return _make_adapter()


# Since fixture is fragile due to __new__, use a simpler approach:

def _make_adapter():
    a = MqttAdapter.__new__(MqttAdapter)
    a.robot_id = "robot1"
    a.broker_host = "localhost"
    a.broker_port = 1883
    a.username = None
    a._password = None
    a.use_tls = False
    a.client_id = "test_client"
    a.client = None
    a._connected = False
    a._command_consumer_task = None
    a._subscribe_tasks = []
    a._connect_timeout_s = 8.0
    a._max_incoming_payload_bytes = 256 * 1024
    a._last_disconnect_log_time = 0.0
    a._disconnect_log_interval = 10.0
    return a


# ── Topic building ───────────────────────────────────────────────────

class TestTopicBuilding:
    def test_command_topic(self):
        a = _make_adapter()
        assert a._get_command_topic("navigateTo") == "aroc/robot/robot1/commands/navigateTo"

    def test_status_topic(self):
        a = _make_adapter()
        assert a._get_status_topic("navigation") == "aroc/robot/robot1/status/navigation"


# ── _decode_json_payload ─────────────────────────────────────────────

class TestDecodeJsonPayload:
    def test_valid_bytes(self):
        a = _make_adapter()
        payload = json.dumps({"command_id": "c1"}).encode("utf-8")
        result = a._decode_json_payload(payload, topic="test")
        assert result == {"command_id": "c1"}

    def test_valid_string(self):
        a = _make_adapter()
        result = a._decode_json_payload('{"key": "val"}', topic="test")
        assert result == {"key": "val"}

    def test_invalid_json(self):
        a = _make_adapter()
        result = a._decode_json_payload(b"not json", topic="test")
        assert result is None

    def test_oversized_payload(self):
        a = _make_adapter()
        a._max_incoming_payload_bytes = 10
        result = a._decode_json_payload(b"x" * 100, topic="test")
        assert result is None

    def test_non_dict_wrapped(self):
        a = _make_adapter()
        result = a._decode_json_payload(b"[1,2,3]", topic="test")
        assert result == {"value": [1, 2, 3]}


# ── publish_navigation_status ────────────────────────────────────────

class TestPublishNavigation:
    @pytest.mark.asyncio
    async def test_skip_when_disconnected(self):
        a = _make_adapter()
        a._connected = False
        await a.publish_navigation_status({"status": "idle"})  # should not raise

    @pytest.mark.asyncio
    async def test_publish_connected(self):
        a = _make_adapter()
        a._connected = True
        a.client = MagicMock()
        a.client.publish = AsyncMock()
        await a.publish_navigation_status({"status": "navigating"})
        a.client.publish.assert_awaited_once()


# ── publish_position_status ──────────────────────────────────────────

class TestPublishPosition:
    @pytest.mark.asyncio
    async def test_skip_when_disconnected(self):
        a = _make_adapter()
        await a.publish_position_status({"x": 1})

    @pytest.mark.asyncio
    async def test_publish_connected(self):
        a = _make_adapter()
        a._connected = True
        a.client = MagicMock()
        a.client.publish = AsyncMock()
        await a.publish_position_status({"x": 1, "y": 2})
        a.client.publish.assert_awaited_once()


# ── publish_event ────────────────────────────────────────────────────

class TestPublishEvent:
    @pytest.mark.asyncio
    async def test_skip_when_disconnected(self):
        a = _make_adapter()
        await a.publish_event("ack", {"type": "received"})

    @pytest.mark.asyncio
    async def test_publish_connected(self):
        a = _make_adapter()
        a._connected = True
        a.client = MagicMock()
        a.client.publish = AsyncMock()
        with patch("services.mqtt_adapter.settings") as s:
            s.mqtt_events_topic = lambda kind: f"aroc/robot/robot1/events/{kind}"
            await a.publish_event("ack", {"type": "received"})
        a.client.publish.assert_awaited_once()


# ── publish_command ──────────────────────────────────────────────────

class TestPublishCommand:
    @pytest.mark.asyncio
    async def test_raises_when_disconnected(self):
        a = _make_adapter()
        with pytest.raises(MqttUnavailableError):
            await a.publish_command("navigateTo", {"target_id": "A"})

    @pytest.mark.asyncio
    async def test_publish_connected(self):
        a = _make_adapter()
        a._connected = True
        a.client = MagicMock()
        a.client.publish = AsyncMock()
        await a.publish_command("navigateTo", {"target_id": "A"})
        a.client.publish.assert_awaited_once()


# ── _mark_disconnected ───────────────────────────────────────────────

class TestMarkDisconnected:
    def test_sets_connected_false(self):
        a = _make_adapter()
        a._connected = True
        a.client = None  # avoid real close
        a._mark_disconnected("test")
        assert a._connected is False

    def test_already_disconnected_noop(self):
        a = _make_adapter()
        a._connected = False
        a._mark_disconnected("test")  # should not log/crash


# ── is_connected ─────────────────────────────────────────────────────

class TestIsConnected:
    def test_false_by_default(self):
        a = _make_adapter()
        assert a.is_connected is False

    def test_true_when_connected(self):
        a = _make_adapter()
        a._connected = True
        assert a.is_connected is True


# ── disconnect ───────────────────────────────────────────────────────

class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_noop_when_not_connected(self):
        a = _make_adapter()
        await a.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_disconnect_closes_client(self):
        a = _make_adapter()
        a._connected = True
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock()
        a.client = mock_client
        await a.disconnect()
        assert a._connected is False
        assert a.client is None
