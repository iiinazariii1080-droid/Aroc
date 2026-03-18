"""Tests for MqttCommandBridge — critical code paths.

Covers: _prepare_request_headers, _handle_incoming_message,
_handle_command_message, _execute_http_command, _validate_path edge cases,
and error model consistency.
"""

import json
import time
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock, patch

from path_validator import validate_path

from bridge import MqttCommandBridge
from shared.config_types import BridgeConfig, MQTTConnectionConfig, ServiceConfig
from shared.constants import MAX_TASK_RESULT_QUEUE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> BridgeConfig:
    mqtt_keys = {
        "broker",
        "broker_port",
        "mqtt_user",
        "mqtt_password",
        "robot_id",
        "client_id",
        "mqtt_publish_qos",
        "mqtt_use_tls",
        "mqtt_ca_certs",
        "mqtt_certfile",
        "mqtt_keyfile",
        "mqtt_tls_insecure",
    }
    mqtt_defaults = dict(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="robot-01",
        client_id="bridge-test",
        mqtt_publish_qos=1,
    )
    bridge_defaults = dict(
        http_timeout=2.0,
        task_poll_interval=0.5,
        task_poll_timeout=5.0,
        services=MappingProxyType(
            {
                "robot": ServiceConfig(name="robot", base_url="http://robot:8110/api/v1/robot", watch_tasks=True),
                "igus": ServiceConfig(name="igus", base_url="http://igus:8103/api/v1/igus"),
            }
        ),
        status_heartbeat_interval=15.0,
    )
    mqtt_overrides = {k: v for k, v in overrides.items() if k in mqtt_keys}
    bridge_overrides = {k: v for k, v in overrides.items() if k not in mqtt_keys}
    mqtt_defaults.update(mqtt_overrides)
    bridge_defaults.update(bridge_overrides)
    return BridgeConfig(mqtt=MQTTConnectionConfig(**mqtt_defaults), **bridge_defaults)


def _make_bridge(config: BridgeConfig | None = None) -> MqttCommandBridge:
    """Create a bridge with mocked MQTT and config dependencies."""
    cfg = config or _make_config()
    with patch("bridge.LightMQTTClient") as mock_mqtt_cls, patch("bridge.HeartbeatPublisher"):
        mock_mqtt = MagicMock()
        mock_mqtt.is_connected = True
        mock_mqtt_cls.return_value = mock_mqtt
        bridge = MqttCommandBridge(config=cfg)
    return bridge


# ===========================================================================
# CRITICAL-1: Header injection protection
# ===========================================================================


class TestPrepareRequestHeaders:
    """Verify that security-sensitive headers from MQTT payloads are filtered."""

    def setup_method(self) -> None:
        self.bridge = _make_bridge()

    def test_user_headers_passed_through(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers({"X-Custom": "value", "Accept": "application/json"})
        assert result["X-Custom"] == "value"
        assert result["Accept"] == "application/json"

    def test_authorization_header_blocked(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers({"Authorization": "Bearer evil-token"})
        assert "Authorization" not in result
        assert "authorization" not in result

    def test_authorization_case_insensitive(self) -> None:
        for variant in ("authorization", "AUTHORIZATION", "Authorization", "aUtHoRiZaTiOn"):
            result = self.bridge._http_executor.prepare_request_headers({variant: "Bearer x"})
            assert variant not in result, f"{variant} should be blocked"

    def test_cookie_header_blocked(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers({"Cookie": "session=abc"})
        assert "Cookie" not in result

    def test_host_header_blocked(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers({"Host": "evil.com"})
        assert "Host" not in result

    def test_x_forwarded_headers_blocked(self) -> None:
        headers = {
            "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Host": "evil.com",
            "X-Forwarded-Proto": "https",
        }
        result = self.bridge._http_executor.prepare_request_headers(headers)
        for key in headers:
            assert key not in result, f"{key} should be blocked"

    def test_proxy_headers_blocked(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers(
            {
                "Proxy-Authorization": "Basic abc",
                "Proxy-Connection": "keep-alive",
            }
        )
        assert "Proxy-Authorization" not in result
        assert "Proxy-Connection" not in result

    def test_auth_manager_headers_preserved(self) -> None:
        auth_mgr = MagicMock()
        auth_mgr.auth_headers.return_value = {"Authorization": "Bearer legit"}
        auth_mgr.robot_id.return_value = "robot-01"
        auth_mgr.has_valid_token.return_value = True
        self.bridge._auth_manager = auth_mgr
        result = self.bridge._http_executor.prepare_request_headers({"Authorization": "Bearer evil"})
        assert result["Authorization"] == "Bearer legit"

    def test_none_user_headers(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers(None)
        assert isinstance(result, dict)

    def test_none_values_skipped(self) -> None:
        result = self.bridge._http_executor.prepare_request_headers({"X-Foo": None, "X-Bar": "ok"})
        assert "X-Foo" not in result
        assert result["X-Bar"] == "ok"


# ===========================================================================
# _handle_incoming_message — routing, validation, size limit
# ===========================================================================


class TestHandleIncomingMessage:
    """Tests for the central message routing method."""

    def setup_method(self) -> None:
        self.bridge = _make_bridge()
        self.bridge._resp_publisher.send_response = MagicMock()
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)
        self.bridge._http_executor.submit = MagicMock()

    def test_payload_too_large_returns_413(self) -> None:
        huge = b"x" * (1_048_577)
        topic = "aroc/robot/robot-01/cmd/igus"
        self.bridge._handle_incoming_message(topic, huge)
        call_args = self.bridge._resp_publisher.send_response.call_args
        assert call_args is not None
        payload = call_args[0][1]
        assert payload["status_code"] == 413

    def test_valid_service_message_dispatched(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/status"}).encode()
        topic = "aroc/robot/robot-01/svc/robot"
        self.bridge._handle_incoming_message(topic, data)
        assert self.bridge._http_executor.submit.called

    def test_unknown_service_returns_error(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/x"}).encode()
        topic = "aroc/robot/robot-01/svc/nonexistent"
        self.bridge._handle_incoming_message(topic, data)
        call_args = self.bridge._resp_publisher.send_response.call_args
        assert call_args is not None
        payload = call_args[0][1]
        assert payload["success"] is False
        assert "Unknown service" in str(payload.get("body", {}).get("detail", ""))

    def test_invalid_json_returns_error(self) -> None:
        topic = "aroc/robot/robot-01/cmd/robot"
        self.bridge._handle_incoming_message(topic, b"not-json{{{")
        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is False

    def test_invalid_path_returns_400(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/foo\x00bar"}).encode()
        topic = "aroc/robot/robot-01/cmd/robot"
        self.bridge._handle_incoming_message(topic, data)
        call_args = self.bridge._resp_publisher.send_response.call_args
        assert call_args is not None
        payload = call_args[0][1]
        assert payload["status_code"] == 400

    def test_command_topic_routed_to_command_handler(self) -> None:
        data = json.dumps(
            {
                "command_id": "cmd-1",
                "target_id": "station-A",
            }
        ).encode()
        topic = "aroc/robot/robot-01/commands/navigateTo"
        with patch.object(self.bridge, "_handle_command_message") as mock_cmd:
            self.bridge._handle_incoming_message(topic, data)
            assert mock_cmd.called
            assert mock_cmd.call_args[0][0] == "navigateTo"


# ===========================================================================
# _handle_command_message — dedup, safety, dispatch
# ===========================================================================


class TestHandleCommandMessage:
    """Tests for the command handler dispatch."""

    def setup_method(self) -> None:
        self.bridge = _make_bridge()
        self.bridge._resp_publisher.send_response = MagicMock()
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)
        self.bridge._http_executor.submit = MagicMock()

    def test_non_dict_payload_rejected(self) -> None:
        self.bridge._handle_command_message("navigateTo", "not a dict")
        assert self.bridge._resp_publisher.send_response.called

    def test_missing_command_id_rejected(self) -> None:
        self.bridge._handle_command_message("navigateTo", {"target_id": "A"})
        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is False

    def test_duplicate_command_returns_cached(self) -> None:
        cached = {"type": "ack", "service": "robot", "success": True, "request_id": "cmd-1"}
        self.bridge._dedup.store("cmd-1", cached)
        self.bridge._handle_command_message(
            "navigateTo",
            {
                "command_id": "cmd-1",
                "target_id": "A",
            },
        )
        assert self.bridge._resp_publisher.send_response.called
        assert self.bridge._resp_publisher.send_response.call_args[0][1] == cached

    def test_safety_locked_rejects_navigate(self) -> None:
        self.bridge._safety_gate._safety_state = {"safety_lockout": True, "reason": "e-stop"}
        self.bridge._safety_gate._safety_state_ts = time.time()
        self.bridge._handle_command_message(
            "navigateTo",
            {
                "command_id": "cmd-2",
                "target_id": "A",
            },
        )
        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is False
        assert "safety lockout" in str(payload.get("body", {}).get("detail", "")).lower()

    def test_unsupported_command_rejected(self) -> None:
        self.bridge._handle_command_message(
            "unknown_cmd",
            {
                "command_id": "cmd-3",
            },
        )
        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert "Unknown command" in str(payload.get("body", {}).get("detail", ""))

    def test_navigate_dispatched_to_http(self) -> None:
        self.bridge._safety_gate._safety_state = {"safety_lockout": False}
        self.bridge._safety_gate._safety_state_ts = time.time()
        self.bridge._handle_command_message(
            "navigateTo",
            {
                "command_id": "cmd-4",
                "target_id": "station-A",
            },
        )
        assert self.bridge._http_executor.submit.called
        call_kwargs = self.bridge._http_executor.submit.call_args
        assert "navigate" in call_kwargs.kwargs.get("url", "") or "navigate" in str(call_kwargs)

    def test_estop_not_blocked_by_safety(self) -> None:
        """E-stop commands should bypass safety lockout check."""
        self.bridge._safety_gate._safety_state = {"safety_lockout": True, "reason": "test"}
        self.bridge._safety_gate._safety_state_ts = time.time()

        # Mock the requests.Session created inside the e-stop thread
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        with patch("command_dispatcher.requests.Session", return_value=mock_session):
            self.bridge._handle_command_message(
                "estop",
                {
                    "command_id": "cmd-5",
                },
            )
            self.bridge._command_dispatcher.join_estop_threads(timeout=5)
        # E-stop bypasses safety and goes through synchronous HTTP path
        assert self.bridge._resp_publisher.send_response.called


# ===========================================================================
# _validate_path — edge cases
# ===========================================================================


class TestValidatePath:
    def test_normal_path(self) -> None:
        assert validate_path("/tasks/navigate") == "/tasks/navigate"

    def test_null_byte_rejected(self) -> None:
        assert validate_path("/foo\x00bar") is None

    def test_backslash_rejected(self) -> None:
        assert validate_path("/foo\\bar") is None

    def test_traversal_to_os_path_rejected(self) -> None:
        # /api/../etc/passwd normalizes to /etc/passwd — blocked as OS path (SSRF mitigation)
        assert validate_path("/api/../etc/passwd") is None

    def test_traversal_beyond_root_rejected(self) -> None:
        assert validate_path("/../../etc/passwd") is None

    def test_at_sign_rejected(self) -> None:
        assert validate_path("/foo@bar") is None

    def test_carriage_return_rejected(self) -> None:
        assert validate_path("/foo\rbar") is None

    def test_relative_path_gets_slash(self) -> None:
        result = validate_path("tasks/status")
        assert result is not None
        assert result.startswith("/")

    def test_semicolon_rejected(self) -> None:
        assert validate_path("/foo;bar") is None

    def test_encoded_semicolon_rejected(self) -> None:
        assert validate_path("/foo%3Bbar") is None


# ===========================================================================
# _execute_http_command — success and error paths
# ===========================================================================


class TestExecuteHttpCommand:
    def setup_method(self) -> None:
        self.bridge = _make_bridge()
        self.bridge._resp_publisher.send_response = MagicMock()
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)

    def test_success_path(self) -> None:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"task_id": "t-1"}
        mock_response.headers = {"content-type": "application/json"}

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        with patch.object(self.bridge._http_executor, "get_http_session", return_value=mock_session):
            self.bridge._http_executor._execute(
                service="robot",
                request_id="r1",
                method="POST",
                url="http://robot:8110/api/v1/robot/tasks/navigate",
                headers={},
                body={"target_id": "A"},
                timeout=2.0,
                service_cfg=ServiceConfig(name="robot", base_url="http://robot:8110/api/v1/robot", watch_tasks=True),
            )

        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is True
        assert payload["status_code"] == 200

    def test_http_error_path(self) -> None:
        import requests

        mock_session = MagicMock()
        mock_session.request.side_effect = requests.ConnectionError("refused")

        with patch.object(self.bridge._http_executor, "get_http_session", return_value=mock_session):
            self.bridge._http_executor._execute(
                service="robot",
                request_id="r1",
                method="POST",
                url="http://robot:8110/api/v1/robot/tasks/navigate",
                headers={},
                body={},
                timeout=2.0,
            )

        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is False
        assert payload["error"]["type"] == "http_error"

    def test_forbidden_headers_not_sent_to_downstream(self) -> None:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.headers = {}

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        with patch.object(self.bridge._http_executor, "get_http_session", return_value=mock_session):
            self.bridge._http_executor._execute(
                service="robot",
                request_id="r1",
                method="GET",
                url="http://robot:8110/api/v1/robot/status",
                headers={"Authorization": "Bearer evil", "X-Custom": "ok"},
                body=None,
                timeout=2.0,
            )

        actual_headers = mock_session.request.call_args.kwargs["headers"]
        assert "Authorization" not in actual_headers or actual_headers.get("Authorization") != "Bearer evil"
        assert actual_headers.get("X-Custom") == "ok"


# ===========================================================================
# Timeout parameter validation
# ===========================================================================


class TestTimeoutValidation:
    """Tests for timeout parameter type/range validation."""

    def setup_method(self) -> None:
        self.bridge = _make_bridge()
        self.bridge._resp_publisher.send_response = MagicMock()
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)
        self.bridge._http_executor.submit = MagicMock()

    def test_string_timeout_rejected(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/status", "timeout": "abc"}).encode()
        topic = "aroc/robot/robot-01/svc/robot"
        self.bridge._handle_incoming_message(topic, data)
        call_args = self.bridge._resp_publisher.send_response.call_args
        assert call_args is not None
        payload = call_args[0][1]
        assert payload["success"] is False
        assert payload["status_code"] == 400
        self.bridge._http_executor.submit.assert_not_called()

    def test_boolean_timeout_rejected(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/status", "timeout": True}).encode()
        topic = "aroc/robot/robot-01/svc/robot"
        self.bridge._handle_incoming_message(topic, data)
        call_args = self.bridge._resp_publisher.send_response.call_args
        assert call_args is not None
        payload = call_args[0][1]
        assert payload["success"] is False

    def test_valid_numeric_timeout_accepted(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/status", "timeout": 10.0}).encode()
        topic = "aroc/robot/robot-01/svc/robot"
        self.bridge._handle_incoming_message(topic, data)
        assert self.bridge._http_executor.submit.called

    def test_timeout_clamped_to_max(self) -> None:
        data = json.dumps({"request_id": "r1", "method": "GET", "path": "/status", "timeout": 9999}).encode()
        topic = "aroc/robot/robot-01/svc/robot"
        self.bridge._handle_incoming_message(topic, data)
        assert self.bridge._http_executor.submit.called
        call_kwargs = self.bridge._http_executor.submit.call_args.kwargs
        assert call_kwargs["timeout"] <= 300.0


# ===========================================================================
# _publish_json_parse_error — was calling undefined method
# ===========================================================================


class TestJsonParseError:
    """Tests for _publish_json_parse_error (previously had undefined method bug)."""

    def setup_method(self) -> None:
        self.bridge = _make_bridge()
        self.bridge._resp_publisher.send_response = MagicMock()
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)

    def test_json_parse_error_on_service_topic(self) -> None:
        """Should publish error response (not raise AttributeError)."""
        self.bridge._resp_publisher.publish_json_parse_error("aroc/robot/robot-01/cmd/robot", "Unexpected token")
        assert self.bridge._resp_publisher.send_response.called
        payload = self.bridge._resp_publisher.send_response.call_args[0][1]
        assert payload["success"] is False
        assert payload["status_code"] == 400

    def test_json_parse_error_on_unknown_topic(self) -> None:
        """Topics with no extractable service should publish to error topic."""
        self.bridge._resp_publisher.publish_json = MagicMock(return_value=True)
        self.bridge._resp_publisher.publish_json_parse_error("aroc/robot/robot-01/other", "Invalid JSON")
        # Short topic with no name segment — falls through to error topic
        # _extract_service returns None for topics without a valid service
        # If service found, goes to _publish_error_response; else publishes to errors topic
        assert self.bridge._resp_publisher.send_response.called or self.bridge._resp_publisher.publish_json.called


# ===========================================================================
# Pending result queue behavior
# ===========================================================================


class TestPendingResultQueue:
    """Tests for pending result queue behavior."""

    def test_result_queued_when_disconnected(self) -> None:
        """Results go to pending queue when MQTT is disconnected."""
        bridge = _make_bridge()
        bridge.mqtt_client.is_connected = False
        bridge._resp_publisher.send_response("robot", {"request_id": "req-1", "success": True})
        assert bridge._pending_queue.has_pending
        assert bridge._pending_queue.pending_count == 1

    def test_queue_evicts_oldest_when_full(self) -> None:
        """When pending queue reaches MAX_TASK_RESULT_QUEUE, oldest entry evicted."""
        bridge = _make_bridge()
        bridge.mqtt_client.is_connected = False
        for i in range(MAX_TASK_RESULT_QUEUE + 1):
            bridge._pending_queue.queue(f"req-{i}", {"success": True})
        assert bridge._pending_queue.pending_count == MAX_TASK_RESULT_QUEUE
