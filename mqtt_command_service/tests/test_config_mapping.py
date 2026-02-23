"""Tests for app.utils.config_mapping."""


from app.models.schemas import BrokerConfigUpdate
from app.utils.config_mapping import (
    CONFIG_KEY_TO_FIELD,
    FIELD_TO_CONFIG_KEY,
    apply_config_updates_to_dict,
    prepare_config_updates,
)


class TestFieldMappings:
    def test_reverse_mapping(self):
        """CONFIG_KEY_TO_FIELD is the exact inverse of FIELD_TO_CONFIG_KEY."""
        for field, key in FIELD_TO_CONFIG_KEY.items():
            assert CONFIG_KEY_TO_FIELD[key] == field


class TestPrepareConfigUpdates:
    def test_all_fields(self):
        update = BrokerConfigUpdate(
            broker="mqtt.example.com",
            broker_port=8883,
            mqtt_user="user1",
            mqtt_password="pass1",
            mqtt_use_tls=True,
            mqtt_tls_insecure=False,
        )
        result = prepare_config_updates(update)
        assert result["MQTT_BROKER"] == "mqtt.example.com"
        assert result["MQTT_PORT"] == "8883"
        assert result["MQTT_USER"] == "user1"
        assert result["MQTT_PASS"] == "pass1"
        assert result["MQTT_USE_TLS"] == "true"
        assert result["MQTT_TLS_INSECURE"] == "false"

    def test_partial_update(self):
        update = BrokerConfigUpdate(broker="new-broker.local")
        result = prepare_config_updates(update)
        assert result == {"MQTT_BROKER": "new-broker.local"}

    def test_empty_update(self):
        update = BrokerConfigUpdate()
        assert prepare_config_updates(update) == {}


class TestApplyConfigUpdates:
    def test_string_values(self):
        config = {}
        updates = {"MQTT_BROKER": "host", "MQTT_USER": "admin"}
        result = apply_config_updates_to_dict(config, updates)
        assert result["MQTT_BROKER"] == "host"

    def test_port_conversion(self):
        config = {}
        result = apply_config_updates_to_dict(config, {"MQTT_PORT": "1883"})
        assert result["MQTT_PORT"] == 1883
        assert isinstance(result["MQTT_PORT"], int)

    def test_tls_bool_conversion(self):
        config = {}
        result = apply_config_updates_to_dict(config, {"MQTT_USE_TLS": "true"})
        assert result["mqtt_use_tls"] is True  # mapped via CONFIG_KEY_TO_FIELD

    def test_invalid_port_skipped(self):
        config = {"MQTT_PORT": 1883}
        result = apply_config_updates_to_dict(config, {"MQTT_PORT": "not-a-number"})
        # Original value preserved because conversion fails
        assert result["MQTT_PORT"] == 1883

    def test_cert_paths_empty(self):
        config = {}
        result = apply_config_updates_to_dict(config, {"MQTT_CA_CERTS": ""})
        assert result.get("MQTT_CA_CERTS") is None
