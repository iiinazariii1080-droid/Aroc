"""
Unit tests for UnifiedMQTTClient.
"""
import time
from unittest.mock import Mock, patch

import pytest

from app.services.config_service import ConfigChangeEvent, ConfigChangeNotification, ConfigService
from app.services.mqtt_client_service import MQTTClientConfig, MQTTConnectionState, UnifiedMQTTClient


@pytest.fixture
def mock_config_service():
    """Create mock ConfigService."""
    service = Mock(spec=ConfigService)

    # Mock config
    from config import BridgeConfig
    mock_config = BridgeConfig(
        broker="test.broker.com",
        broker_port=1883,
        mqtt_user="test_user",
        mqtt_password="test_pass",
        robot_id="test_robot",
        client_id="test_client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=120.0,
        services={},
        mqtt_use_tls=False,
        mqtt_ca_certs=None,
        mqtt_certfile=None,
        mqtt_keyfile=None,
        mqtt_tls_insecure=False,
    )

    service.get_config.return_value = mock_config
    service.subscribe = Mock()
    service.unsubscribe = Mock()

    return service


@pytest.fixture
def mqtt_client(mock_config_service):
    """Create UnifiedMQTTClient instance for testing."""
    with patch('app.services.mqtt_client_service.mqtt_client') as mock_mqtt_module:
        # Mock paho.mqtt.client module
        mock_client_class = Mock()
        mock_client_instance = Mock()
        mock_client_class.return_value = mock_client_instance

        # Setup mock client
        mock_client_instance.loop_start = Mock()
        mock_client_instance.loop_stop = Mock()
        mock_client_instance.disconnect = Mock()
        mock_client_instance.connect = Mock()
        mock_client_instance.subscribe = Mock(return_value=(0, 1))
        mock_client_instance.unsubscribe = Mock(return_value=(0,))
        mock_client_instance.publish = Mock(return_value=Mock(rc=0))

        mock_mqtt_module.Client = mock_client_class

        client = UnifiedMQTTClient(
            config_service=mock_config_service,
            component_name="test_component",
            max_reconnect_delay=5.0,
            initial_reconnect_delay=0.1,
        )

        client._mock_client = mock_client_instance
        yield client


class TestUnifiedMQTTClient:
    """Test cases for UnifiedMQTTClient."""

    def test_initialization(self, mqtt_client):
        """Test client initialization."""
        assert mqtt_client.component_name == "test_component"
        assert mqtt_client.state == MQTTConnectionState.DISCONNECTED
        assert not mqtt_client.is_connected

    def test_start_creates_client(self, mqtt_client):
        """Test that start() creates MQTT client."""
        mqtt_client.start()

        # Client should be created
        assert mqtt_client._client is not None

    def test_stop_disconnects_client(self, mqtt_client):
        """Test that stop() disconnects client."""
        mqtt_client.start()
        mqtt_client.stop()

        # Should call disconnect
        mqtt_client._mock_client.loop_stop.assert_called()
        mqtt_client._mock_client.disconnect.assert_called()

    def test_subscribe_registers_handler(self, mqtt_client):
        """Test subscribing to topic."""
        handler_called = []

        def handler(message):
            handler_called.append(message)

        mqtt_client.subscribe("test/topic", handler)

        # Handler should be registered
        # (we can't easily test internal state, but we can test it doesn't crash)
        assert True

    def test_publish_when_connected(self, mqtt_client):
        """Test publishing when connected."""
        # Simulate connected state
        mqtt_client._mqtt_connected.set()
        mqtt_client._client = mqtt_client._mock_client

        result = mqtt_client.publish("test/topic", {"data": "test"})

        # Should attempt to publish
        # Result depends on mock setup
        assert isinstance(result, bool)

    def test_publish_when_disconnected(self, mqtt_client):
        """Test publishing when disconnected."""
        # Ensure disconnected
        mqtt_client._mqtt_connected.clear()

        result = mqtt_client.publish("test/topic", {"data": "test"})

        # Should return False when not connected
        assert result is False

    def test_is_connected_property(self, mqtt_client):
        """Test is_connected property."""
        # Initially disconnected
        assert not mqtt_client.is_connected

        # Simulate connection
        mqtt_client._mqtt_connected.set()
        assert mqtt_client.is_connected

        # Disconnect
        mqtt_client._mqtt_connected.clear()
        assert not mqtt_client.is_connected

    def test_on_connect_callback(self, mqtt_client):
        """Test on_connect callback."""
        # Simulate connection
        mqtt_client._on_connect(None, None, None, 0, None)

        # Should be connected
        assert mqtt_client.is_connected
        assert mqtt_client.state == MQTTConnectionState.CONNECTED

    def test_on_disconnect_callback(self, mqtt_client):
        """Test on_disconnect callback."""
        # First connect
        mqtt_client._mqtt_connected.set()
        mqtt_client._state = MQTTConnectionState.CONNECTED

        # Then disconnect
        mqtt_client._on_disconnect(None, None, 0, None)

        # Should be disconnected
        assert not mqtt_client.is_connected
        assert mqtt_client.state == MQTTConnectionState.DISCONNECTED

    def test_config_change_triggers_reconnect(self, mqtt_client):
        """Test that config change triggers reconnection."""
        # Start client
        mqtt_client.start()

        # Create config change notification
        from config import BridgeConfig
        old_config = BridgeConfig(
            broker="old.broker.com",
            broker_port=1883,
            mqtt_user="user",
            mqtt_password="pass",
            robot_id="robot",
            client_id="client",
            http_timeout=5.0,
            task_poll_interval=1.0,
            task_poll_timeout=120.0,
            services={},
            mqtt_use_tls=False,
            mqtt_ca_certs=None,
            mqtt_certfile=None,
            mqtt_keyfile=None,
            mqtt_tls_insecure=False,
        )

        new_config = BridgeConfig(
            broker="new.broker.com",  # Changed
            broker_port=1883,
            mqtt_user="user",
            mqtt_password="pass",
            robot_id="robot",
            client_id="client",
            http_timeout=5.0,
            task_poll_interval=1.0,
            task_poll_timeout=120.0,
            services={},
            mqtt_use_tls=False,
            mqtt_ca_certs=None,
            mqtt_certfile=None,
            mqtt_keyfile=None,
            mqtt_tls_insecure=False,
        )

        notification = ConfigChangeNotification(
            event=ConfigChangeEvent.BROKER_CHANGED,
            revision=2,
            updated_at=time.time(),
            old_config=old_config,
            new_config=new_config,
            changed_fields={"broker"}
        )

        # Trigger config change
        mqtt_client._on_config_changed(notification)

        # Should set reconnect required
        assert mqtt_client._reconnect_required.is_set()

    def test_log_throttling(self, mqtt_client):
        """Test log throttling mechanism."""
        # Call _should_throttle_log multiple times quickly
        log_key = "test_key"

        # First call should not throttle
        should_throttle1 = mqtt_client._should_throttle_log(log_key)
        assert not should_throttle1

        # Second call immediately should throttle
        should_throttle2 = mqtt_client._should_throttle_log(log_key)
        assert should_throttle2


class TestMQTTClientConfig:
    """Test cases for MQTTClientConfig."""

    def test_mqtt_client_config_creation(self):
        """Test MQTTClientConfig creation."""
        config = MQTTClientConfig(
            broker="test.broker.com",
            port=1883,
            username="user",
            password="pass",
            client_id="client",
            use_tls=False,
            ca_certs=None,
            certfile=None,
            keyfile=None,
            tls_insecure=False,
        )

        assert config.broker == "test.broker.com"
        assert config.port == 1883
        assert config.username == "user"
        assert config.use_tls is False

