"""
Integration tests for bridge.py with refactored code.

These tests check for bugs in the integration between:
- bridge.py and payload validation utilities
- bridge.py and constants/Enum
- Message type handling
"""
import concurrent.futures
import json
from unittest.mock import Mock, patch

import pytest
from paho.mqtt.client import MQTTMessage


def _parse_mqtt_payload(raw):
    """Parse MQTT publish payload (may be a pre-serialized JSON string or dict)."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


class _SyncExecutor:
    """Executor that runs tasks synchronously for deterministic tests."""

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

from bridge import MqttCommandBridge
from config import BridgeConfig, ServiceConfig
from constants import ErrorType, MessageType, NavigationState, StatusType


@pytest.fixture
def mock_bridge_config():
    """Create mock bridge config."""
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
            "robot": ServiceConfig(
                name="robot",
                base_url="http://localhost:8110",
                watch_tasks=True,
            )
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
def bridge_instance(mock_bridge_config):
    """Create bridge instance with mocked dependencies."""
    with patch('bridge.get_config_service') as mock_get_config:
        mock_config_service = Mock()
        mock_config_service.get_config.return_value = mock_bridge_config
        mock_config_service.subscribe = Mock()
        mock_config_service.unsubscribe = Mock()
        mock_get_config.return_value = mock_config_service

        with patch('bridge.UnifiedMQTTClient') as mock_mqtt_client_class:
            mock_mqtt_client = Mock()
            mock_mqtt_client.is_connected = True
            mock_mqtt_client.subscribe = Mock()
            mock_mqtt_client.unsubscribe = Mock()
            mock_mqtt_client.publish = Mock(return_value=True)
            mock_mqtt_client.start = Mock()
            mock_mqtt_client.stop = Mock()
            mock_mqtt_client_class.return_value = mock_mqtt_client

            bridge = MqttCommandBridge(config=mock_bridge_config)
            bridge.mqtt_client = mock_mqtt_client
            bridge._http_executor = _SyncExecutor()
            yield bridge


class TestMessageTypeConstants:
    """Test that message types use constants correctly."""

    def test_ack_message_type(self, bridge_instance):
        """Test: ACK messages use MessageType.ACK constant."""
        bridge_instance._publish_error_ack(
            service="robot",
            request_id="test-123",
            status_code=400,
            body={"detail": "Test error"},
            error={"type": ErrorType.COMMAND_ERROR.value, "message": "Test"}
        )

        # Check that publish was called with correct type
        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["type"] == MessageType.ACK.value

    def test_status_message_type(self, bridge_instance):
        """Test: Status messages use MessageType.STATUS constant."""
        bridge_instance._publish_system_status()

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["type"] == MessageType.STATUS.value
        assert published_payload["status_type"] == StatusType.SYSTEM.value

    def test_navigation_status_type(self, bridge_instance):
        """Test: Navigation status uses correct constants."""
        context = {
            "command_id": "cmd-123",
            "command_name": "navigateTo",
            "status_type": StatusType.NAVIGATION.value,
        }

        bridge_instance._publish_navigation_status(
            state=NavigationState.COMPLETED.value,
            success=True,
            detail={"result": "ok"},
            context=context
        )

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["type"] == MessageType.STATUS.value
        assert published_payload["status_type"] == StatusType.NAVIGATION.value
        assert published_payload["state"] == NavigationState.COMPLETED.value


class TestPayloadValidationIntegration:
    """Test integration with payload validation utilities."""

    def test_handle_incoming_message_with_invalid_json(self, bridge_instance):
        """Test: Invalid JSON is handled correctly."""
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = b"invalid json {"

        bridge_instance._handle_mqtt_message(message)

        # Should publish error response
        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["type"] == MessageType.ACK.value
        assert published_payload["success"] is False
        assert published_payload["error"]["type"] == ErrorType.INVALID_JSON.value

    def test_handle_incoming_message_with_non_dict(self, bridge_instance):
        """Test: Non-dict payload is handled correctly."""
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = json.dumps("not a dict").encode()

        bridge_instance._handle_mqtt_message(message)

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["success"] is False
        assert published_payload["error"]["type"] == ErrorType.INVALID_JSON.value

    def test_handle_command_with_missing_command_id(self, bridge_instance):
        """Test: Command without command_id is rejected."""
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/commands/navigateTo"  # Must be bytes
        message.payload = json.dumps({"target_id": "target-1"}).encode()

        bridge_instance._handle_mqtt_message(message)

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["success"] is False
        assert "command_id is required" in published_payload["body"]["detail"].lower()

    def test_handle_command_with_invalid_headers(self, bridge_instance):
        """Test: Command with invalid headers is rejected."""
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/commands/navigateTo"  # Must be bytes
        message.payload = json.dumps({
            "command_id": "cmd-123",
            "target_id": "target-1",
            "headers": "not a dict"  # Invalid headers
        }).encode()

        bridge_instance._handle_mqtt_message(message)

        # Invalid headers are validated in _handle_navigate_command
        # which calls _publish_command_error
        bridge_instance.mqtt_client.publish.assert_called()

        # Find the error message
        all_calls = bridge_instance.mqtt_client.publish.call_args_list
        error_payload = None
        for call_args in all_calls:
            payload = _parse_mqtt_payload(call_args[0][1])
            if payload.get("type") == MessageType.ACK.value and not payload.get("success", True):
                error_payload = payload
                break

        assert error_payload is not None, "Error message not found"
        assert error_payload["success"] is False
        # Headers validation happens in _handle_navigate_command,
        # which publishes command error, not invalid_json error
        assert "error" in error_payload or "body" in error_payload


class TestErrorTypeConstants:
    """Test that error types use constants correctly."""

    def test_routing_error_type(self, bridge_instance):
        """Test: Routing errors use ErrorType.ROUTING_ERROR."""
        bridge_instance._publish_unknown_service_response("unknown_service", "req-123")

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["error"]["type"] == ErrorType.ROUTING_ERROR.value

    def test_command_error_type(self, bridge_instance):
        """Test: Command errors use ErrorType.COMMAND_ERROR."""
        bridge_instance._publish_command_error("navigateTo", "cmd-123", "Test error")

        # _publish_command_error calls _publish_error_ack which publishes to resp topic
        # and also _publish_navigation_status which publishes to status topic
        # Check all published messages
        assert bridge_instance.mqtt_client.publish.call_count >= 1

        # Get all call arguments
        all_calls = bridge_instance.mqtt_client.publish.call_args_list

        # Find the ACK message (should be in resp topic)
        ack_payload = None
        for call_args in all_calls:
            topic = call_args[0][0]
            payload = _parse_mqtt_payload(call_args[0][1])
            if "resp" in topic and payload.get("type") == MessageType.ACK.value:
                ack_payload = payload
                break

        # Check that error field exists and has correct type
        assert ack_payload is not None, "ACK message not found"
        assert "error" in ack_payload
        assert ack_payload["error"]["type"] == ErrorType.COMMAND_ERROR.value

    def test_http_error_type(self, bridge_instance):
        """Test: HTTP errors use ErrorType.HTTP_ERROR."""
        bridge_instance._publish_http_error_response("robot", "req-123", "Connection failed")

        bridge_instance.mqtt_client.publish.assert_called()
        call_args = bridge_instance.mqtt_client.publish.call_args
        published_payload = _parse_mqtt_payload(call_args[0][1])

        assert published_payload["error"]["type"] == ErrorType.HTTP_ERROR.value


class TestEdgeCases:
    """Test edge cases that might reveal bugs."""

    def test_empty_payload(self, bridge_instance):
        """Test: Empty payload handling."""
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = b""

        bridge_instance._handle_mqtt_message(message)

        # Should handle gracefully
        bridge_instance.mqtt_client.publish.assert_called()

    def test_very_large_payload(self, bridge_instance):
        """Test: Very large payload handling."""
        large_payload = {"data": "x" * 1000000}  # 1MB of data
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = json.dumps(large_payload).encode()

        # Should handle or reject gracefully
        bridge_instance._handle_mqtt_message(message)

        # Check that it was handled (either processed or rejected)
        assert bridge_instance.mqtt_client.publish.called

    def test_unicode_in_payload(self, bridge_instance):
        """Test: Unicode characters in payload."""
        unicode_payload = {
            "request_id": "req-123",
            "method": "GET",
            "path": "/status",
            "data": "Привет 你好 🌍"
        }
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = json.dumps(unicode_payload, ensure_ascii=False).encode('utf-8')

        bridge_instance._handle_mqtt_message(message)

        # Should handle unicode correctly
        bridge_instance.mqtt_client.publish.assert_called()

    def test_none_in_dict_values(self, bridge_instance):
        """Test: None values in dictionary."""
        payload = {
            "request_id": "req-123",
            "method": "GET",
            "path": "/status",
            "body": None,
            "headers": None
        }
        message = MQTTMessage()
        message._topic = b"aroc/robot/test_robot/cmd/robot"  # Must be bytes
        message.payload = json.dumps(payload).encode()

        bridge_instance._handle_mqtt_message(message)

        # Should handle None values gracefully
        bridge_instance.mqtt_client.publish.assert_called()

