"""BridgeResponsePublisher: MQTT response and error publishing.

Centralizes all response, error, and status publishing for the bridge.
"""

import logging
from collections.abc import Callable
from typing import Any

from command_dispatcher import COMMAND_SPECS
from mqtt_publisher import MQTTResponsePublisher, PublishResult
from payload_models import AckPayload, ErrorDetail

from shared.config_types import TopicSchema
from shared.constants import (
    DEFAULT_COMMAND_SERVICE,
    MAX_MQTT_PAYLOAD_SIZE,
    ErrorType,
    MessageType,
    NavigationState,
    StatusType,
)
from shared.metrics import mqtt_messages_total
from shared.mqtt_client import LightMQTTClient
from shared.utils import now_iso

logger = logging.getLogger(__name__)


class BridgeResponsePublisher:
    """Publishes MQTT responses, error messages, and navigation status."""

    def __init__(
        self,
        publisher: MQTTResponsePublisher,
        mqtt_client: LightMQTTClient,
        get_topics_fn: Callable[[], TopicSchema],
        get_robot_id_fn: Callable[[], str],
        extract_service_fn: Callable[[str], str | None],
    ) -> None:
        self._publisher = publisher
        self._mqtt_client = mqtt_client
        self._get_topics = get_topics_fn
        self._get_robot_id = get_robot_id_fn
        self._queue_pending: Callable[[str, dict[str, Any]], None] | None = None
        self._extract_service = extract_service_fn

    def set_pending_queue(self, queue_fn: Callable[[str, dict[str, Any]], None]) -> None:
        """Wire the pending result queue after initialization.

        Must be called before any ``send_response`` that might need to
        queue a result (i.e., before ``mqtt_client.start()``).
        """
        if not callable(queue_fn):
            raise TypeError(f"queue_fn must be callable, got {type(queue_fn).__name__}")
        self._queue_pending = queue_fn

    def publish_json(self, topic: str, payload: dict[str, Any]) -> PublishResult:
        return self._publisher.publish_json(topic, payload)

    def publish_status(self, suffix: str, payload: dict[str, Any]) -> None:
        topic = f"{self._get_topics().status_base}/{suffix}"
        if self.publish_json(topic, payload) != PublishResult.SUCCESS:
            logger.warning("[bridge] Failed to publish status to %s", topic)

    def send_response(self, service: str, payload: dict[str, Any]) -> None:
        request_id = payload.get("request_id")
        if self._mqtt_client.is_connected:
            topic = f"{self._get_topics().resp_base}/{service}"
            result = self.publish_json(topic, payload)
            if result == PublishResult.SUCCESS:
                return
            if result == PublishResult.PAYLOAD_TOO_LARGE:
                mqtt_messages_total.labels(service="bridge", direction="outbound", topic_type="dropped").inc()
                logger.error(
                    "[bridge] Response dropped permanently (too large) for service=%s request_id=%s "
                    "command_id=%s keys=%s",
                    service,
                    request_id,
                    payload.get("command_id"),
                    list(payload.keys()),
                )
                return
        if self._queue_pending is None:
            logger.error(
                "[bridge] Cannot queue pending result — pending queue not wired (service=%s request_id=%s)",
                service, request_id,
            )
            return
        if request_id:
            self._queue_pending(request_id, payload)
        else:
            # Generate fallback key so the result can be delivered when MQTT reconnects
            fallback_key = f"_nrid_{service}_{payload.get('command_id', id(payload))}"
            logger.error(
                "[bridge] MQTT unavailable, no request_id — queuing with fallback key %s (service=%s)",
                fallback_key,
                service,
            )
            self._queue_pending(fallback_key, payload)

    def publish_navigation_status(
        self,
        state: str,
        success: bool | None,
        detail: Any,
        context: dict[str, Any] | None,
    ) -> None:
        if not context or context.get("status_type") != StatusType.NAVIGATION.value:
            return
        command_id = context.get("command_id")
        if not command_id:
            return
        payload = {
            "type": MessageType.STATUS.value,
            "status_type": StatusType.NAVIGATION.value,
            "robot_id": self._get_robot_id(),
            "timestamp": now_iso(),
            "command_id": command_id,
            "command_name": context.get("command_name"),
            "target_id": context.get("target_id"),
            "task_id": context.get("task_id"),
            "state": state,
            "success": success,
            "detail": detail,
        }
        self.publish_status("navigation", payload)

    def publish_error_response(
        self,
        service: str,
        status_code: int,
        detail: str,
        error: ErrorDetail,
        request_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {"detail": detail}
        if extra_body:
            body.update(extra_body)
        ack = AckPayload(
            request_id=request_id,
            service=service,
            success=False,
            status_code=status_code,
            body=body,
            error=error,
        )
        self.send_response(service, ack.to_dict())

    def publish_command_error(self, command: str, command_id: str | None, message: str) -> None:
        error_detail = {"detail": message, "command": command}
        self.publish_error_response(
            DEFAULT_COMMAND_SERVICE,
            400,
            message,
            ErrorDetail.command_error(message),
            request_id=command_id,
            extra_body={"command": command},
        )
        if command.lower() in COMMAND_SPECS:
            self.publish_navigation_status(
                state=NavigationState.REJECTED.value,
                success=False,
                detail=error_detail,
                context={"command_name": command.lower(), "command_id": command_id, "status_type": "navigation"},
            )

    def publish_processing_error(self, topic: str, error_message: str) -> None:
        try:
            service = self._extract_service(topic)
            if not service:
                if "/cmd/" in topic or "/commands/" in topic:
                    error_topic = f"{self._get_topics().status_base}/errors"
                    self.publish_json(
                        error_topic,
                        {
                            "type": MessageType.ERROR.value,
                            "topic": topic,
                            "error": {
                                "type": ErrorType.PROCESSING_ERROR.value,
                                "message": error_message,
                                "timestamp": now_iso(),
                            },
                        },
                    )
                return
            self.publish_error_response(
                service,
                500,
                f"Failed to process message: {error_message}",
                ErrorDetail.processing_error(error_message),
            )
        except Exception as e:
            logger.error("Failed to publish processing error: %s", e, exc_info=True)

    def publish_json_parse_error(self, topic: str, error_message: str) -> None:
        try:
            service = self._extract_service(topic)
            if service:
                self.publish_error_response(
                    service,
                    400,
                    "Invalid JSON payload",
                    ErrorDetail.invalid_json(error_message),
                )
            else:
                error_topic = f"{self._get_topics().status_base}/errors"
                self.publish_json(
                    error_topic,
                    {
                        "type": MessageType.ERROR.value,
                        "topic": topic,
                        "error": {
                            "type": ErrorType.INVALID_JSON.value,
                            "message": error_message,
                            "timestamp": now_iso(),
                        },
                    },
                )
        except Exception as e:
            logger.error("Failed to publish JSON parse error: %s", e, exc_info=True)

    def reject_oversized_payload(self, topic: str, size: int) -> None:
        logger.warning(
            "[bridge] Payload too large (%d bytes, max %d) on topic %s — dropped",
            size,
            MAX_MQTT_PAYLOAD_SIZE,
            topic,
        )
        service = self._extract_service(topic)
        if service:
            ack = AckPayload(
                request_id=None,
                service=service,
                success=False,
                status_code=413,
                body={"detail": f"Payload too large ({size} bytes, max {MAX_MQTT_PAYLOAD_SIZE})"},
                error=ErrorDetail.command_error("Payload exceeds size limit"),
            )
            self.send_response(service, ack.to_dict())
