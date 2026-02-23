"""Tests for bridge payload limit and flush queue logic."""
import json
from unittest.mock import MagicMock, Mock

import pytest


class TestPayloadSizeLimit:
    """Tests for _MAX_PAYLOAD_BYTES enforcement in _handle_incoming_message."""

    def test_oversized_payload_is_dropped(self):
        """Payloads > _MAX_PAYLOAD_BYTES must be dropped (no further processing)."""
        from bridge import MqttCommandBridge

        bridge = MagicMock(spec=MqttCommandBridge)
        bridge._MAX_PAYLOAD_BYTES = 1_048_576
        # Call real method
        MqttCommandBridge._handle_incoming_message(
            bridge,
            "aroc/robot/test/cmd/robot",
            b"x" * (bridge._MAX_PAYLOAD_BYTES + 1),
        )
        # Should NOT attempt to process (no service extraction, no JSON parse)
        bridge._extract_config_key.assert_not_called()

    def test_payload_within_limit_is_processed(self):
        """Payload within limit should pass to processing."""
        from bridge import MqttCommandBridge

        bridge = MagicMock(spec=MqttCommandBridge)
        bridge._MAX_PAYLOAD_BYTES = 1_048_576
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/"}).encode()
        MqttCommandBridge._handle_incoming_message(
            bridge,
            "aroc/robot/test/cmd/robot",
            payload,
        )
        # Should attempt processing (at least try to extract config key)
        bridge._extract_config_key.assert_called_once()


class TestFlushTaskResultQueue:
    """Tests for _flush_task_result_queue removing items only after success."""

    @pytest.fixture
    def bridge(self):
        """Create a minimal bridge-like object with queue attributes."""
        import threading

        class FakeBridge:
            _task_result_queue = {}
            _task_result_queue_lock = threading.Lock()
            _flushing_queue = False

        return FakeBridge()

    def test_removes_only_after_success(self, bridge):
        """Items removed only after successful send; failed items stay."""
        from bridge import MqttCommandBridge

        bridge._task_result_queue = {
            "req1": {"service": "robot", "data": "ok"},
            "req2": {"service": "robot", "data": "fail"},
        }

        def side_effect(service, result):
            if result["data"] == "fail":
                raise RuntimeError("publish failed")

        bridge._send_response = side_effect

        MqttCommandBridge._flush_task_result_queue(bridge)

        assert "req1" not in bridge._task_result_queue
        assert "req2" in bridge._task_result_queue
        assert bridge._flushing_queue is False

    def test_concurrent_guard(self, bridge):
        """Concurrent flush is blocked by _flushing_queue flag."""
        from bridge import MqttCommandBridge

        bridge._task_result_queue = {"req1": {"service": "s", "data": "d"}}
        bridge._flushing_queue = True
        bridge._send_response = Mock()

        MqttCommandBridge._flush_task_result_queue(bridge)

        bridge._send_response.assert_not_called()

    def test_empty_queue_is_noop(self, bridge):
        """Empty queue returns immediately without setting flushing flag."""
        from bridge import MqttCommandBridge

        bridge._send_response = Mock()

        MqttCommandBridge._flush_task_result_queue(bridge)

        bridge._send_response.assert_not_called()
        assert bridge._flushing_queue is False
