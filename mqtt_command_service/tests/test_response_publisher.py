"""Unit tests for BridgeResponsePublisher.

Covers: send_response routing (connected/disconnected), pending queue fallback,
navigation status publishing, command error publishing, processing error,
JSON parse error, oversized payload rejection.
No time.sleep().
"""

from unittest.mock import MagicMock, call

from mqtt_publisher import PublishResult
from response_publisher import BridgeResponsePublisher

from shared.config_types import TopicSchema
from shared.constants import MAX_MQTT_PAYLOAD_SIZE, NavigationState, StatusType


def _make_publisher(
    *,
    publish_result: PublishResult = PublishResult.SUCCESS,
    connected: bool = True,
    robot_id: str = "test-bot",
) -> tuple[BridgeResponsePublisher, MagicMock, MagicMock, MagicMock]:
    """Create BridgeResponsePublisher with controllable fakes.

    Returns (publisher, mock_low_level_publisher, mock_queue_pending, mock_mqtt_client).
    """
    mock_publisher = MagicMock()
    mock_publisher.publish_json.return_value = publish_result

    mock_mqtt_client = MagicMock()
    mock_mqtt_client.is_connected = connected

    topics = TopicSchema(robot_id=robot_id)
    queue_pending = MagicMock()

    pub = BridgeResponsePublisher(
        publisher=mock_publisher,
        mqtt_client=mock_mqtt_client,
        get_topics_fn=lambda: topics,
        get_robot_id_fn=lambda: robot_id,
        extract_service_fn=lambda topic: topics.parse_service(topic),
    )
    pub.set_pending_queue(queue_pending)
    return pub, mock_publisher, queue_pending, mock_mqtt_client


class TestSendResponse:
    def test_connected_success_publishes_to_correct_topic(self):
        pub, mock_pub, queue, _ = _make_publisher()
        pub.send_response("robot", {"request_id": "req-1", "success": True})
        mock_pub.publish_json.assert_called_once()
        topic = mock_pub.publish_json.call_args[0][0]
        assert topic == "aroc/robot/test-bot/resp/robot"

    def test_connected_success_does_not_queue(self):
        pub, _, queue, _ = _make_publisher()
        pub.send_response("robot", {"request_id": "req-1"})
        queue.assert_not_called()

    def test_connected_payload_too_large_not_queued(self):
        pub, _, queue, _ = _make_publisher(publish_result=PublishResult.PAYLOAD_TOO_LARGE)
        pub.send_response("robot", {"request_id": "req-1"})
        queue.assert_not_called()

    def test_connected_transient_failure_queues_with_request_id(self):
        pub, _, queue, _ = _make_publisher(publish_result=PublishResult.TRANSIENT_FAILURE)
        pub.send_response("robot", {"request_id": "req-1"})
        queue.assert_called_once_with("req-1", {"request_id": "req-1"})

    def test_disconnected_with_request_id_queues(self):
        pub, mock_pub, queue, _ = _make_publisher(connected=False)
        pub.send_response("robot", {"request_id": "req-1"})
        mock_pub.publish_json.assert_not_called()
        queue.assert_called_once_with("req-1", {"request_id": "req-1"})

    def test_disconnected_no_request_id_queues_with_fallback_key(self):
        pub, _, queue, _ = _make_publisher(connected=False)
        pub.send_response("robot", {"command_id": "cmd-1"})
        queue.assert_called_once()
        key = queue.call_args[0][0]
        assert key.startswith("_nrid_robot_")
        assert "cmd-1" in key

    def test_disconnected_no_request_id_no_command_id_fallback_key(self):
        pub, _, queue, _ = _make_publisher(connected=False)
        payload = {"service": "robot"}
        pub.send_response("robot", payload)
        queue.assert_called_once()
        key = queue.call_args[0][0]
        assert key.startswith("_nrid_robot_")


class TestPublishNavigationStatus:
    def test_valid_navigation_context_publishes(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_navigation_status(
            state=NavigationState.ACKNOWLEDGED.value,
            success=True,
            detail={"some": "data"},
            context={
                "command_name": "navigateTo",
                "command_id": "cmd-1",
                "status_type": StatusType.NAVIGATION.value,
            },
        )
        mock_pub.publish_json.assert_called_once()
        topic = mock_pub.publish_json.call_args[0][0]
        assert topic == "aroc/robot/test-bot/status/navigation"
        payload = mock_pub.publish_json.call_args[0][1]
        assert payload["state"] == "acknowledged"
        assert payload["command_id"] == "cmd-1"
        assert payload["robot_id"] == "test-bot"
        assert payload["success"] is True

    def test_non_navigation_status_type_is_noop(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_navigation_status(
            state="acknowledged",
            success=True,
            detail={},
            context={"status_type": "system", "command_id": "cmd-1"},
        )
        mock_pub.publish_json.assert_not_called()

    def test_no_context_is_noop(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_navigation_status(state="acknowledged", success=True, detail={}, context=None)
        mock_pub.publish_json.assert_not_called()

    def test_no_command_id_is_noop(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_navigation_status(
            state="acknowledged",
            success=True,
            detail={},
            context={"status_type": StatusType.NAVIGATION.value},
        )
        mock_pub.publish_json.assert_not_called()


class TestPublishCommandError:
    def test_publishes_error_response_and_navigation_reject(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_command_error("navigateTo", "cmd-1", "target_id is required")

        # Should publish error response + navigation status (2 calls)
        assert mock_pub.publish_json.call_count == 2

        # First call: error response to resp_base/robot
        first_topic = mock_pub.publish_json.call_args_list[0][0][0]
        assert "/resp/" in first_topic

        # Second call: navigation rejected status
        second_topic = mock_pub.publish_json.call_args_list[1][0][0]
        assert "status/navigation" in second_topic
        nav_payload = mock_pub.publish_json.call_args_list[1][0][1]
        assert nav_payload["state"] == NavigationState.REJECTED.value

    def test_unknown_command_no_navigation_status(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_command_error("unknownCmd", "cmd-1", "Unknown command")
        # Only error response, no navigation status for unknown commands
        assert mock_pub.publish_json.call_count == 1


class TestPublishProcessingError:
    def test_with_known_service_publishes_error_response(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_processing_error(
            "aroc/robot/test-bot/commands/robot",
            "Processing failed",
        )
        mock_pub.publish_json.assert_called_once()
        topic = mock_pub.publish_json.call_args[0][0]
        assert "/resp/" in topic

    def test_without_service_cmd_topic_publishes_to_errors(self):
        """When extract_service returns None but topic contains /cmd/, publish to errors topic."""
        # Use a custom extract_service that always returns None
        mock_publisher = MagicMock()
        mock_publisher.publish_json.return_value = PublishResult.SUCCESS
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected = True
        topics = TopicSchema(robot_id="test-bot")
        pub = BridgeResponsePublisher(
            publisher=mock_publisher,
            mqtt_client=mock_mqtt,
            get_topics_fn=lambda: topics,
            get_robot_id_fn=lambda: "test-bot",
            extract_service_fn=lambda topic: None,  # Always returns None
        )
        pub.set_pending_queue(MagicMock())
        pub.publish_processing_error(
            "aroc/robot/test-bot/cmd/something",
            "Processing failed",
        )
        mock_publisher.publish_json.assert_called_once()
        topic = mock_publisher.publish_json.call_args[0][0]
        assert "status" in topic and "errors" in topic

    def test_exception_in_publish_does_not_propagate(self):
        pub, mock_pub, _, _ = _make_publisher()
        mock_pub.publish_json.side_effect = RuntimeError("boom")
        # Should not raise
        pub.publish_processing_error("aroc/robot/test-bot/commands/robot", "error")


class TestPublishJsonParseError:
    def test_with_service_publishes_error(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.publish_json_parse_error(
            "aroc/robot/test-bot/commands/robot",
            "Invalid JSON at pos 5",
        )
        mock_pub.publish_json.assert_called_once()
        payload = mock_pub.publish_json.call_args[0][1]
        assert payload["status_code"] == 400

    def test_without_service_publishes_to_errors_topic(self):
        pub, mock_pub, _, _ = _make_publisher()
        # Topic with only 4 parts — parse_service returns None
        pub.publish_json_parse_error(
            "aroc/robot/test-bot/unknown",
            "Invalid JSON",
        )
        mock_pub.publish_json.assert_called_once()
        topic = mock_pub.publish_json.call_args[0][0]
        assert "errors" in topic
        payload = mock_pub.publish_json.call_args[0][1]
        assert payload["error"]["type"] == "invalid_json"


class TestRejectOversizedPayload:
    def test_publishes_413_response(self):
        pub, mock_pub, _, _ = _make_publisher()
        pub.reject_oversized_payload(
            "aroc/robot/test-bot/commands/robot",
            MAX_MQTT_PAYLOAD_SIZE + 1,
        )
        mock_pub.publish_json.assert_called_once()
        payload = mock_pub.publish_json.call_args[0][1]
        assert payload["status_code"] == 413
        assert payload["success"] is False
