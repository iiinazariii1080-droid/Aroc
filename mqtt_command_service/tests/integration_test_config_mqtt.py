"""
Integration tests for ConfigService and UnifiedMQTTClient interaction.
"""
import time
from unittest.mock import Mock, patch

import pytest

from app.services.config_service import ConfigService
from app.services.mqtt_client_service import UnifiedMQTTClient


@pytest.fixture
def mock_config_service():
    """Create mock ConfigService with real behavior."""
    from config import BridgeConfig

    config = BridgeConfig(
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

    service = Mock(spec=ConfigService)
    service.get_config.return_value = config
    service.subscribe = Mock()
    service.unsubscribe = Mock()

    return service


class TestConfigMQTTIntegration:
    """Integration tests for ConfigService and MQTTClient."""

    def test_mqtt_client_subscribes_to_config_changes(self, mock_config_service):
        """Test that MQTT client subscribes to config service."""
        with patch('app.services.mqtt_client_service.mqtt_client') as mock_mqtt_module:
            mock_client_class = Mock()
            mock_client_instance = Mock()
            mock_client_class.return_value = mock_client_instance
            mock_client_instance.loop_start = Mock()
            mock_client_instance.connect = Mock()
            mock_mqtt_module.Client = mock_client_class

            UnifiedMQTTClient(
                config_service=mock_config_service,
                component_name="test"
            )

            # Should subscribe to config changes
            mock_config_service.subscribe.assert_called_once()

    def test_config_change_triggers_mqtt_reconnect(self, mock_config_service):
        """Test that config change triggers MQTT reconnection."""
        with patch('app.services.mqtt_client_service.mqtt_client') as mock_mqtt_module:
            mock_client_class = Mock()
            mock_client_instance = Mock()
            mock_client_class.return_value = mock_client_instance
            mock_client_instance.loop_start = Mock()
            mock_client_instance.loop_stop = Mock()
            mock_client_instance.disconnect = Mock()
            mock_client_instance.connect = Mock()
            mock_mqtt_module.Client = mock_client_class

            client = UnifiedMQTTClient(
                config_service=mock_config_service,
                component_name="test"
            )

            client.start()

            # Simulate config change
            from app.services.config_service import ConfigChangeEvent, ConfigChangeNotification
            from config import BridgeConfig

            old_config = mock_config_service.get_config()
            new_config = BridgeConfig(
                broker="new.broker.com",  # Changed
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

            notification = ConfigChangeNotification(
                event=ConfigChangeEvent.BROKER_CHANGED,
                revision=2,
                updated_at=time.time(),
                old_config=old_config,
                new_config=new_config,
                changed_fields={"broker"}
            )

            # Get the callback that was registered
            subscribe_call = mock_config_service.subscribe.call_args
            callback = subscribe_call[0][0] if subscribe_call else None

            if callback:
                # Call the callback (simulating config change)
                callback(notification)

                # Should trigger reconnect
                assert client._reconnect_required.is_set()

            client.stop()

