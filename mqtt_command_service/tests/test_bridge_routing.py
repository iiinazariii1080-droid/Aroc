"""
Tests for bridge message routing, topic extraction, path validation,
and command dedup integration.
"""
import concurrent.futures
import json
from unittest.mock import Mock, patch

import pytest
from paho.mqtt.client import MQTTMessage

from bridge import MqttCommandBridge
from config import BridgeConfig, ServiceConfig
from constants import ErrorType, MessageType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_mqtt_payload(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _make_mqtt_message(topic: str, payload: bytes) -> MQTTMessage:
    msg = MQTTMessage()
    msg._topic = topic.encode("utf-8")
    msg.payload = payload
    return msg


class _SyncExecutor:
    """Runs futures synchronously for deterministic tests."""

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            result = fn(*args, **kwargs)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        return future

    def shutdown(self, wait=True):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bridge_config():
    return BridgeConfig(
        broker="test.broker.com",
        broker_port=1883,
        mqtt_user="test_user",
        mqtt_password="test_pass",
        robot_id="test_robot",
        client_id="test_client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=120.0,
        services={
            "robot": ServiceConfig(name="robot", base_url="http://localhost:8110", watch_tasks=True),
            "igus": ServiceConfig(name="igus", base_url="http://localhost:8111", watch_tasks=False),
        },
        mqtt_use_tls=False,
        mqtt_ca_certs=None,
        mqtt_certfile=None,
        mqtt_keyfile=None,
        mqtt_tls_insecure=False,
        mqtt_publish_qos=1,
        status_heartbeat_interval=15.0,
    )


@pytest.fixture
def bridge(mock_bridge_config):
    """Create a bridge instance with mocked MQTT and HTTP."""
    with patch("bridge.get_config_service") as mock_get_config:
        mock_cs = Mock()
        mock_cs.get_config.return_value = mock_bridge_config
        mock_cs.subscribe = Mock()
        mock_cs.unsubscribe = Mock()
        mock_get_config.return_value = mock_cs

        with patch("bridge.UnifiedMQTTClient") as mock_mqtt_cls:
            mock_mqtt = Mock()
            mock_mqtt.is_connected = True
            mock_mqtt.publish = Mock(return_value=True)
            mock_mqtt.subscribe = Mock()
            mock_mqtt.start = Mock()
            mock_mqtt.stop = Mock()
            mock_mqtt_cls.return_value = mock_mqtt

            b = MqttCommandBridge(config=mock_bridge_config)
            b.mqtt_client = mock_mqtt
            b._http_executor = _SyncExecutor()
            yield b


# ===================================================================
# 1. Topic extraction helpers (pure unit tests)
# ===================================================================

class TestExtractService:
    def test_valid_service_topic(self, bridge):
        assert bridge._extract_service("aroc/robot/r1/cmd/robot") == "robot"

    def test_short_topic_returns_none(self, bridge):
        assert bridge._extract_service("aroc/robot/r1") is None

    def test_five_part_topic(self, bridge):
        assert bridge._extract_service("a/b/c/d/svc") == "svc"


class TestExtractCommand:
    def test_valid_command_topic(self, bridge):
        assert bridge._extract_command("aroc/robot/r1/commands/navigateTo") == "navigateTo"

    def test_non_command_topic(self, bridge):
        # 'cmd' segment instead of 'commands'
        assert bridge._extract_command("aroc/robot/r1/cmd/robot") is None

    def test_short_topic(self, bridge):
        assert bridge._extract_command("aroc/robot") is None


class TestExtractConfigKey:
    def test_valid_config_topic(self, bridge):
        assert bridge._extract_config_key("aroc/robot/r1/config/MQTT_BROKER") == "MQTT_BROKER"

    def test_non_config_topic(self, bridge):
        assert bridge._extract_config_key("aroc/robot/r1/cmd/robot") is None

    def test_short_topic(self, bridge):
        assert bridge._extract_config_key("aroc/robot") is None


# ===================================================================
# 2. Path validation (pure unit tests)
# ===================================================================

class TestValidatePath:
    def test_clean_path(self):
        assert MqttCommandBridge._validate_path("/status") == "/status"

    def test_path_without_leading_slash(self):
        result = MqttCommandBridge._validate_path("status")
        assert result is not None and result.startswith("/")

    def test_traversal_blocked(self):
        # Path that resolves above root after normalization
        assert MqttCommandBridge._validate_path("foo/../../bar") is None

    def test_null_byte_blocked(self):
        assert MqttCommandBridge._validate_path("/foo\x00bar") is None

    def test_backslash_blocked(self):
        assert MqttCommandBridge._validate_path("/foo\\bar") is None

    def test_at_sign_blocked(self):
        assert MqttCommandBridge._validate_path("/foo@bar") is None

    def test_dot_segments_normalized(self):
        result = MqttCommandBridge._validate_path("/a/b/../c")
        assert result == "/a/c"


# ===================================================================
# 3. URL construction
# ===================================================================

class TestBuildHttpUrl:
    def test_known_service(self, bridge):
        url = bridge._build_http_url("robot", "/status")
        assert url == "http://localhost:8110/status"

    def test_unknown_service(self, bridge):
        assert bridge._build_http_url("nonexistent", "/status") is None

    def test_path_without_slash(self, bridge):
        url = bridge._build_http_url("robot", "status")
        assert url == "http://localhost:8110/status"


# ===================================================================
# 4. Incoming message routing
# ===================================================================

class TestIncomingMessageRouting:
    """Tests for _handle_incoming_message dispatch logic."""

    def test_oversized_payload_dropped(self, bridge):
        big = b"x" * (bridge._MAX_PAYLOAD_BYTES + 1)
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", big)
        bridge.mqtt_client.publish.assert_not_called()

    def test_config_topic_dispatched(self, bridge):
        bridge._handle_config_message = Mock()
        payload = json.dumps({"MQTT_BROKER": "new.host"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/config/MQTT_BROKER", payload)
        bridge._handle_config_message.assert_called_once_with("MQTT_BROKER", {"MQTT_BROKER": "new.host"})

    def test_command_topic_dispatched(self, bridge):
        bridge._handle_command_message = Mock()
        payload = json.dumps({"command_id": "c1"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/commands/navigateTo", payload)
        bridge._handle_command_message.assert_called_once_with("navigateTo", {"command_id": "c1"})

    def test_service_topic_dispatched(self, bridge):
        """Known service triggers HTTP submission."""
        bridge._submit_http = Mock()
        payload = json.dumps({
            "request_id": "req1",
            "method": "GET",
            "path": "/status",
        }).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._submit_http.assert_called_once()
        assert bridge._submit_http.call_args.kwargs["service"] == "robot"

    def test_unknown_service_gets_error(self, bridge):
        payload = json.dumps({
            "request_id": "req1",
            "method": "GET",
            "path": "/x",
        }).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/unknown_svc", payload)
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["error"]["type"] == ErrorType.ROUTING_ERROR.value

    def test_bad_json_publishes_error(self, bridge):
        bridge._handle_incoming_message(
            "aroc/robot/r1/cmd/robot",
            b"not json{",
        )
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["success"] is False

    def test_unsafe_path_rejected(self, bridge):
        payload = json.dumps({
            "request_id": "req1",
            "method": "GET",
            "path": "/foo\\bar",
        }).encode()
        bridge._submit_http = Mock()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._submit_http.assert_not_called()
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["status_code"] == 400


# ===================================================================
# 5. Command dedup integration
# ===================================================================

class TestCommandDedupIntegration:
    """Tests that _handle_command_message correctly uses CommandDeduplicator."""

    def _dispatch_command(self, bridge, command, payload_dict):
        bridge._handle_incoming_message(
            f"aroc/robot/r1/commands/{command}",
            json.dumps(payload_dict).encode(),
        )

    def test_duplicate_in_flight_ignored(self, bridge):
        """Second identical command_id while first is in-flight is silently ignored."""
        bridge._handle_navigate_command = Mock()
        self._dispatch_command(bridge, "navigateTo", {
            "command_id": "dup1", "target_id": "t1",
        })
        bridge._handle_navigate_command.assert_called_once()

        bridge._handle_navigate_command.reset_mock()
        self._dispatch_command(bridge, "navigateTo", {
            "command_id": "dup1", "target_id": "t1",
        })
        bridge._handle_navigate_command.assert_not_called()

    def test_cached_result_replayed(self, bridge):
        """If a result was cached, re-sending the same command_id replays it."""
        # Store a cached result manually
        bridge._dedup.store("replay1", {
            "service": "robot",
            "type": MessageType.ACK.value,
            "success": True,
            "command_id": "replay1",
        })
        # Also need to make sure it's NOT in flight
        # (store doesn't put it in flight by default)
        self._dispatch_command(bridge, "navigateTo", {
            "command_id": "replay1",
            "target_id": "t1",
        })
        # Should replay via _send_response and NOT call handler
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["success"] is True
        assert body["command_id"] == "replay1"

    def test_unsupported_command_rejected(self, bridge):
        """An unknown command name returns an error and releases the in-flight lock."""
        self._dispatch_command(bridge, "selfDestruct", {
            "command_id": "u1",
        })
        assert bridge._dedup.in_flight_count == 0  # lock released
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["success"] is False
        assert "unsupported" in body["body"]["detail"].lower()

    def test_missing_command_id_rejected(self, bridge):
        self._dispatch_command(bridge, "navigateTo", {"target_id": "t1"})
        call = bridge.mqtt_client.publish.call_args
        body = _parse_mqtt_payload(call[0][1])
        assert body["success"] is False
        assert "command_id" in body["body"]["detail"].lower()


# ===================================================================
# 6. Heartbeat loop
# ===================================================================

class TestHeartbeat:
    def test_heartbeat_publishes_system_and_connection(self, bridge):
        """A single heartbeat iteration publishes system and connection status."""
        bridge._publish_system_status()
        bridge._publish_connection_status()
        assert bridge.mqtt_client.publish.call_count == 2
        topics = [c[0][0] for c in bridge.mqtt_client.publish.call_args_list]
        assert any("system" in t for t in topics)
        assert any("connection" in t for t in topics)

    def test_system_status_contains_uptime(self, bridge):
        bridge._publish_system_status()
        body = _parse_mqtt_payload(bridge.mqtt_client.publish.call_args[0][1])
        assert "uptime_seconds" in body
        assert body["uptime_seconds"] >= 0

    def test_connection_status_contains_mqtt_info(self, bridge):
        bridge._publish_connection_status()
        body = _parse_mqtt_payload(bridge.mqtt_client.publish.call_args[0][1])
        assert body["mqtt"]["connected"] is True
        assert body["mqtt"]["client_id"] == "test_client"


# ===================================================================
# 7. Config message handling
# ===================================================================

class TestConfigMessageRouting:
    def test_known_config_key_dispatched(self, bridge):
        bridge._handle_broker_config_change = Mock()
        bridge._handle_config_message("MQTT_BROKER", {"MQTT_BROKER": "new.host"})
        bridge._handle_broker_config_change.assert_called_once()

    def test_unknown_config_key_ignored(self, bridge):
        # Should not raise; logs a warning
        bridge._handle_config_message("UNKNOWN_KEY", {"foo": "bar"})

    def test_non_dict_config_payload_rejected(self, bridge):
        # list payload should be rejected by validate_dict_payload
        bridge._handle_config_message("MQTT_BROKER", [1, 2, 3])
        bridge.mqtt_client.publish.assert_not_called()


# ===================================================================
# 8. Long-operation timeout auto-increase
# ===================================================================

class TestLongOperationTimeout:
    def test_igus_move_gets_auto_timeout(self, bridge):
        """igus /move should get auto-increased timeout."""
        bridge._submit_http = Mock()
        payload = json.dumps({
            "request_id": "req-long",
            "method": "POST",
            "path": "/move",
            "body": {"target": 1},
        }).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/igus", payload)
        bridge._submit_http.assert_called_once()
        timeout_val = bridge._submit_http.call_args.kwargs.get("timeout")
        assert timeout_val is not None
        assert timeout_val >= 30.0

    def test_normal_service_no_auto_timeout(self, bridge):
        """robot /status should NOT get auto-increased timeout."""
        bridge._submit_http = Mock()
        payload = json.dumps({
            "request_id": "req-normal",
            "method": "GET",
            "path": "/status",
        }).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._submit_http.assert_called_once()
        timeout_val = bridge._submit_http.call_args.kwargs.get("timeout")
        assert timeout_val is None
