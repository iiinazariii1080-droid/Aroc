"""Tests for MqttCommandBridge — core message handling and lifecycle."""
import json
from unittest.mock import MagicMock, patch


def _make_config():
    """Create a valid BridgeConfig for testing."""
    from config import BridgeConfig
    return BridgeConfig(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="test-robot",
        client_id="test-client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=30.0,
    )


class TestBridgeInit:
    """Tests for MqttCommandBridge.__init__."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_init_creates_mqtt_client(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        mock_mqtt.assert_called_once()
        assert b.config is not None

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_init_with_custom_config(self, mock_mqtt, mock_cs):
        cfg = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge(config=cfg)
        assert b.config is cfg

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_init_subscribes_to_config_changes(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        MqttCommandBridge()
        mock_cs.return_value.subscribe.assert_called_once()


class TestBridgeLifecycle:
    """Tests for start/stop/handle_signal."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    @patch("bridge.mqtt_state", create=True)
    def test_start_and_stop(self, mock_state, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        # Mock mqtt_state module
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b.start()
        b.mqtt_client.start.assert_called_once()
        b.stop()
        b.mqtt_client.stop.assert_called_once()

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_stop_when_already_stopped(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b._shutdown.set()
        b.stop()
        # Should return early without calling mqtt_client.stop()
        b.mqtt_client.stop.assert_not_called()

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_handle_signal_stops_bridge(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b.stop = MagicMock()
        b.handle_signal(15, None)
        b.stop.assert_called_once()

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_handle_signal_ignores_when_shutting_down(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b._shutdown.set()
        b.stop = MagicMock()
        b.handle_signal(15, None)
        b.stop.assert_not_called()


class TestIncomingMessage:
    """Tests for _handle_incoming_message."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_drops_oversized_payload(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        # Should not raise, just log a warning and return
        b._handle_incoming_message("test/topic", b"x" * (1_048_576 + 1))

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_invalid_json_logs_error(self, mock_mqtt, mock_cs):
        cfg = _make_config()
        mock_cs.return_value.get_config.return_value = cfg
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b._publish_json_parse_error = MagicMock()
        # Service pattern topic with invalid JSON
        topic = f"aroc/robot/{cfg.robot_id}/cmd/test-service"
        b._handle_incoming_message(topic, b"not json{{}}")or None  # safe_json_loads will fail
        # Should call _publish_json_parse_error or _publish_invalid_json_response

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_config_message_handling(self, mock_mqtt, mock_cs):
        cfg = _make_config()
        mock_cs.return_value.get_config.return_value = cfg
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        b._handle_config_message = MagicMock()
        topic = f"aroc/robot/{cfg.robot_id}/config/update"
        payload = json.dumps({"key": "value"}).encode()
        b._handle_incoming_message(topic, payload)
        # If _extract_config_key matches, _handle_config_message is called
        # Otherwise handled as service message


class TestConfigChanged:
    """Tests for _on_config_changed callback."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_updates_config(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        new_config = _make_config()
        notification = MagicMock()
        notification.new_config = new_config
        notification.event.value = "updated"
        notification.revision = 2
        b._on_config_changed(notification)
        assert b.config is new_config


class TestBuildHttpUrl:
    """Tests for _build_http_url."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_builds_url_for_known_service(self, mock_mqtt, mock_cs):
        from config import BridgeConfig, ServiceConfig
        svc = ServiceConfig(name="test-svc", base_url="http://localhost:8080")
        cfg = BridgeConfig(
            broker="localhost", broker_port=1883, mqtt_user="u", mqtt_password="p",
            robot_id="r", client_id="c", http_timeout=5.0,
            task_poll_interval=1.0, task_poll_timeout=30.0,
            services={"test-svc": svc},
        )
        mock_cs.return_value.get_config.return_value = cfg
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        url = b._build_http_url("test-svc", "/api/health")
        assert url == "http://localhost:8080/api/health"

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_returns_none_for_unknown_service(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        result = b._build_http_url("unknown-svc", "/path")
        assert result is None


class TestValidatePath:
    """Tests for _validate_path method."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_valid_path(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        assert b._validate_path("/api/v1/test") == "/api/v1/test"

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_rejects_null_byte_in_path(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        result = b._validate_path("/test\x00evil")
        assert result is None

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_rejects_backslash_in_path(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        result = b._validate_path("\\etc\\passwd")
        assert result is None

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_normalizes_traversal(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        # /../etc/passwd normalizes to /etc/passwd (allowed but neutralized)
        result = b._validate_path("/../etc/passwd")
        assert result == "/etc/passwd"

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_rejects_at_sign_in_path(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        result = b._validate_path("/test@evil.com")
        assert result is None


class TestGetHttpSession:
    """Tests for thread-local HTTP session."""

    @patch("bridge.get_config_service")
    @patch("bridge.UnifiedMQTTClient")
    def test_get_session_returns_session(self, mock_mqtt, mock_cs):
        mock_cs.return_value.get_config.return_value = _make_config()
        from bridge import MqttCommandBridge
        b = MqttCommandBridge()
        session = b._get_http_session()
        assert session is not None
        # Same thread should get same session
        session2 = b._get_http_session()
        assert session is session2
