"""Contract tests: Config-API broker response schema <-> broker_fetch consumer.

Verifies that the Pydantic schema used by config-api to serialize broker config
is compatible with the field mapping in broker_fetch.build_mqtt_connection_config().

If config-api changes its response shape, these tests fail before production.
"""

from schemas import BrokerConfigResponse
from shared.broker_fetch import build_mqtt_connection_config


def _make_api_response(**overrides) -> dict:
    """Create a valid config-api broker response dict."""
    defaults = dict(
        broker="mqtt.example.com",
        broker_port=1883,
        mqtt_user="robot-user",
        mqtt_password="secret",
        mqtt_use_tls=False,
        mqtt_tls_insecure=False,
    )
    defaults.update(overrides)
    return defaults


class TestContractBrokerConfig:
    def test_api_response_schema_produces_valid_dict(self):
        """BrokerConfigResponse can serialize all fields that broker_fetch expects."""
        data = _make_api_response()
        response = BrokerConfigResponse(**data)
        # model_dump with exclude to check non-redacted fields
        dumped = response.model_dump()
        assert "broker" in dumped
        assert "broker_port" in dumped
        assert "mqtt_user" in dumped
        assert "mqtt_use_tls" in dumped
        assert "mqtt_tls_insecure" in dumped

    def test_consumer_parses_api_response_fields(self):
        """build_mqtt_connection_config correctly maps all API response fields."""
        data = _make_api_response()
        config = build_mqtt_connection_config(
            data, robot_id="test-bot", client_id="test-client", publish_qos=1
        )
        assert config.broker == "mqtt.example.com"
        assert config.broker_port == 1883
        assert config.mqtt_user == "robot-user"
        assert config.mqtt_password == "secret"
        assert config.mqtt_use_tls is False
        assert config.mqtt_tls_insecure is False
        assert config.robot_id == "test-bot"
        assert config.client_id == "test-client"
        assert config.mqtt_publish_qos == 1

    def test_tls_fields_propagate(self):
        """TLS-related fields are correctly propagated from API to consumer."""
        data = _make_api_response(mqtt_use_tls=True, mqtt_tls_insecure=True, broker_port=8883)
        config = build_mqtt_connection_config(
            data, robot_id="bot", client_id="cl", publish_qos=0
        )
        assert config.mqtt_use_tls is True
        assert config.mqtt_tls_insecure is True

    def test_missing_optional_fields_use_defaults(self):
        """Consumer handles missing optional fields with defaults."""
        # Minimal response — only required fields
        data = {"broker": "localhost", "broker_port": 1883, "mqtt_password": "pw"}
        config = build_mqtt_connection_config(
            data, robot_id="bot", client_id="cl", publish_qos=1
        )
        assert config.broker == "localhost"
        assert config.mqtt_user == ""  # default
        assert config.mqtt_use_tls is False  # default
        assert config.mqtt_tls_insecure is False  # default

    def test_schema_field_names_match_consumer_expectations(self):
        """All fields consumed by build_mqtt_connection_config exist in BrokerConfigResponse."""
        consumer_fields = {"broker", "broker_port", "mqtt_user", "mqtt_password", "mqtt_use_tls", "mqtt_tls_insecure"}
        schema_fields = set(BrokerConfigResponse.model_fields.keys())
        missing = consumer_fields - schema_fields
        assert not missing, f"Consumer expects fields not in API schema: {missing}"

    def test_qos_clamped_to_valid_range(self):
        """publish_qos is clamped to 0..2 by the consumer."""
        data = _make_api_response()
        config = build_mqtt_connection_config(data, robot_id="bot", client_id="cl", publish_qos=5)
        assert config.mqtt_publish_qos == 2
        config2 = build_mqtt_connection_config(data, robot_id="bot", client_id="cl", publish_qos=-1)
        assert config2.mqtt_publish_qos == 0


class TestContractNegativePaths:
    """Negative contract tests: schema drift, wrong types, extra fields."""

    def test_extra_unknown_fields_ignored(self):
        """API response with extra fields does not break consumer."""
        data = _make_api_response(new_future_field="value", another_field=42)
        config = build_mqtt_connection_config(
            data, robot_id="bot", client_id="cl", publish_qos=1
        )
        assert config.broker == "mqtt.example.com"

    def test_redacted_password_reaches_consumer_unchanged(self):
        """If password endpoint fails, consumer gets redacted string literally.

        Documents design dependency: password comes from separate endpoint.
        If main response is used directly, consumer gets literal '***REDACTED***'.
        """
        response = BrokerConfigResponse(
            broker="mqtt.example.com",
            broker_port=1883,
            mqtt_user="user",
            mqtt_password="real-secret",
        )
        # Pydantic serializes password as redacted
        dumped = response.model_dump()
        assert dumped["mqtt_password"] == "***REDACTED***"

        # If consumer receives this directly (password endpoint down):
        config = build_mqtt_connection_config(
            dumped, robot_id="bot", client_id="cl", publish_qos=1
        )
        assert config.mqtt_password == "***REDACTED***"  # would fail MQTT auth

    def test_tls_true_without_cert_fields_builds_config(self):
        """TLS=True without cert fields creates valid config (certs resolved at connect time)."""
        data = _make_api_response(mqtt_use_tls=True)
        config = build_mqtt_connection_config(
            data, robot_id="bot", client_id="cl", publish_qos=1
        )
        assert config.mqtt_use_tls is True
        assert config.mqtt_ca_certs is None  # resolved from filesystem at connect time

    def test_empty_broker_string_creates_config(self):
        """Empty broker string creates config (will fail at connect time, not at config build)."""
        data = _make_api_response(broker="")
        config = build_mqtt_connection_config(
            data, robot_id="bot", client_id="cl", publish_qos=1
        )
        assert config.broker == ""
