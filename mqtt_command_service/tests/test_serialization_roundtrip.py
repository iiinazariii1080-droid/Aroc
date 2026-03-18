"""Serialization round-trip tests.

Verifies the encode-publish-decode pipeline:
AckPayload/ErrorDetail → serialize_mqtt_payload → json.loads round-trip.
Tests size limits, compact JSON, unicode preservation, and MQTTResponsePublisher integration.
"""

import json

from mqtt_publisher import MQTTResponsePublisher, PublishResult
from payload_models import AckPayload, ErrorDetail

from shared.constants import MAX_MQTT_PAYLOAD_SIZE
from shared.utils import serialize_mqtt_payload
from tests.conftest import make_bridge_config
from tests.fakes import HonestFakeMQTTClient


class TestSerializeMqttPayload:
    def test_ack_payload_roundtrip(self):
        """AckPayload → to_dict → serialize → json.loads → equals original."""
        ack = AckPayload(
            request_id="req-1",
            service="robot",
            success=True,
            status_code=200,
            body={"task_id": "t-1", "nested": {"key": "value"}},
            command_id="cmd-1",
            command_name="navigateTo",
        )
        original = ack.to_dict()
        result = serialize_mqtt_payload(original)
        assert result is not None
        json_str, encoded = result
        parsed = json.loads(json_str)
        assert parsed["request_id"] == "req-1"
        assert parsed["service"] == "robot"
        assert parsed["success"] is True
        assert parsed["status_code"] == 200
        assert parsed["body"]["task_id"] == "t-1"
        assert parsed["command_id"] == "cmd-1"

    def test_error_detail_all_types_roundtrip(self):
        """All ErrorDetail types survive serialization round-trip."""
        for factory, name in [
            (lambda: ErrorDetail.http_error("connection refused"), "http_error"),
            (lambda: ErrorDetail.command_error("missing target_id"), "command_error"),
            (lambda: ErrorDetail.routing_error("unknown service"), "routing_error"),
            (lambda: ErrorDetail.invalid_json("unexpected token"), "invalid_json"),
            (lambda: ErrorDetail.processing_error("timeout"), "processing_error"),
        ]:
            error = factory()
            payload = {"error": error.to_dict()}
            result = serialize_mqtt_payload(payload)
            assert result is not None
            parsed = json.loads(result[0])
            assert parsed["error"]["type"] == name

    def test_none_values_preserved(self):
        """None values in payload survive round-trip as JSON null."""
        ack = AckPayload(
            request_id=None,
            service="robot",
            success=False,
            status_code=0,
            error=ErrorDetail.http_error("fail"),
        )
        original = ack.to_dict()
        result = serialize_mqtt_payload(original)
        assert result is not None
        parsed = json.loads(result[0])
        assert parsed.get("request_id") is None

    def test_oversized_payload_rejected(self):
        """Payload exceeding MAX_MQTT_PAYLOAD_SIZE returns None."""
        huge = {"data": "x" * (MAX_MQTT_PAYLOAD_SIZE + 1)}
        result = serialize_mqtt_payload(huge)
        assert result is None

    def test_compact_json_no_whitespace(self):
        """Compact separators: no spaces after : or ,"""
        payload = {"key": "value", "num": 42}
        result = serialize_mqtt_payload(payload)
        assert result is not None
        json_str = result[0]
        assert ": " not in json_str
        assert ", " not in json_str
        assert json_str == '{"key":"value","num":42}'

    def test_unicode_preserved_not_escaped(self):
        """Non-ASCII characters preserved as UTF-8, not \\uXXXX escaped."""
        payload = {"robot_name": "робот", "emoji": "🤖", "cjk": "机器人"}
        result = serialize_mqtt_payload(payload)
        assert result is not None
        json_str = result[0]
        # Must contain raw UTF-8, not escaped
        assert "робот" in json_str
        assert "🤖" in json_str
        assert "机器人" in json_str
        assert "\\u" not in json_str
        # Round-trip preserves values
        parsed = json.loads(json_str)
        assert parsed["robot_name"] == "робот"
        assert parsed["emoji"] == "🤖"

    def test_size_boundary_exact(self):
        """Payload at exactly MAX_MQTT_PAYLOAD_SIZE passes; one byte over fails."""
        # Build a payload that's close to the limit
        overhead = len(json.dumps({"d": ""}, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        fill_size = MAX_MQTT_PAYLOAD_SIZE - overhead
        exact = {"d": "a" * fill_size}
        result = serialize_mqtt_payload(exact)
        assert result is not None

        over = {"d": "a" * (fill_size + 1)}
        result_over = serialize_mqtt_payload(over)
        assert result_over is None


class TestMQTTResponsePublisherIntegration:
    def test_publish_json_through_real_publisher(self):
        """MQTTResponsePublisher.publish_json → HonestFakeMQTTClient → json.loads."""
        fake = HonestFakeMQTTClient(start_connected=True)
        fake.start()
        cfg = make_bridge_config()
        publisher = MQTTResponsePublisher(mqtt_client=fake, config=cfg)

        payload = {
            "request_id": "req-rt",
            "service": "robot",
            "success": True,
            "status_code": 200,
            "body": {"task_id": "t-1"},
        }
        result = publisher.publish_json("aroc/robot/test-bot/resp/robot", payload)
        assert result == PublishResult.SUCCESS
        assert len(fake.publish_log) == 1

        topic, payload_str, qos, retain = fake.publish_log[0]
        assert topic == "aroc/robot/test-bot/resp/robot"
        parsed = json.loads(payload_str)
        assert parsed["request_id"] == "req-rt"
        assert parsed["body"]["task_id"] == "t-1"

    def test_publish_json_disconnected_returns_transient_failure(self):
        """Disconnected publish returns TRANSIENT_FAILURE."""
        fake = HonestFakeMQTTClient(start_connected=False)
        fake.start()
        cfg = make_bridge_config()
        publisher = MQTTResponsePublisher(mqtt_client=fake, config=cfg)

        result = publisher.publish_json("test/topic", {"data": 1})
        assert result == PublishResult.TRANSIENT_FAILURE
        assert len(fake.publish_log) == 0

    def test_publish_json_oversized_returns_payload_too_large(self):
        """Oversized payload returns PAYLOAD_TOO_LARGE."""
        fake = HonestFakeMQTTClient(start_connected=True)
        fake.start()
        cfg = make_bridge_config()
        publisher = MQTTResponsePublisher(mqtt_client=fake, config=cfg)

        huge = {"data": "x" * (MAX_MQTT_PAYLOAD_SIZE + 1)}
        result = publisher.publish_json("test/topic", huge)
        assert result == PublishResult.PAYLOAD_TOO_LARGE
        assert len(fake.publish_log) == 0

    def test_publish_fails_while_connected_returns_transient_failure(self):
        """publish_succeeds=False while connected → TRANSIENT_FAILURE."""
        fake = HonestFakeMQTTClient(start_connected=True, publish_succeeds=False)
        fake.start()
        cfg = make_bridge_config()
        publisher = MQTTResponsePublisher(mqtt_client=fake, config=cfg)

        result = publisher.publish_json("test/topic", {"data": 1})
        assert result == PublishResult.TRANSIENT_FAILURE
