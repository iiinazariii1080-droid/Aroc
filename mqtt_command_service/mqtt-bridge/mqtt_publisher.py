"""MQTTResponsePublisher: low-level MQTT JSON publishing for the bridge.

Only the methods actually used by MqttCommandBridge are kept here.
Response-building methods live on MqttCommandBridge (bridge.py).
"""

import enum
import logging
from typing import Any

from shared.config_types import BridgeConfig
from shared.mqtt_client import LightMQTTClient
from shared.utils import serialize_mqtt_payload

logger = logging.getLogger(__name__)


class PublishResult(enum.Enum):
    """Result of an MQTT publish attempt."""

    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    PAYLOAD_TOO_LARGE = "payload_too_large"


class MQTTResponsePublisher:
    """Low-level MQTT JSON publisher used by MqttCommandBridge."""

    def __init__(
        self,
        mqtt_client: LightMQTTClient,
        config: BridgeConfig,
    ) -> None:
        self._mqtt_client = mqtt_client
        self._config = config

    def publish_json(self, topic: str, payload: dict[str, Any]) -> PublishResult:
        """Publish JSON payload to MQTT topic.

        Returns:
            PublishResult.SUCCESS on success,
            PublishResult.PAYLOAD_TOO_LARGE if the serialized message exceeds
                the size limit (caller should drop, not queue for retry),
            PublishResult.TRANSIENT_FAILURE on MQTT disconnection / publish error.
        """
        if not self._mqtt_client.is_connected:
            logger.warning("[bridge] MQTT not connected, skipping publish to %s", topic)
            return PublishResult.TRANSIENT_FAILURE

        try:
            result = serialize_mqtt_payload(payload)
            if result is None:
                logger.error(
                    "[bridge] Payload too large for topic %s — dropped permanently",
                    topic,
                )
                return PublishResult.PAYLOAD_TOO_LARGE

            message, _ = result
            logger.debug("[bridge] MQTT publish %s -> %s", topic, message[:200])

            ok = self._mqtt_client.publish(
                topic,
                message,
                qos=self._config.mqtt_publish_qos,
                retain=False,
            )
            return PublishResult.SUCCESS if ok else PublishResult.TRANSIENT_FAILURE
        except Exception:
            logger.exception("[bridge] Failed to publish MQTT message to %s", topic)
            return PublishResult.TRANSIENT_FAILURE
