"""Tests for bridge.py — command handling, config change, HTTP execution,
publishing helpers, heartbeat, timestamps, queue flushing and error publishing.

Targets the largest coverage gap (~193 missed statements).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from config import BridgeConfig, ServiceConfig


def _make_config(**overrides) -> BridgeConfig:
    defaults = dict(
        broker="localhost",
        broker_port=1883,
        mqtt_user="user",
        mqtt_password="pass",
        robot_id="test-robot",
        client_id="test-client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=30.0,
        services={
            "robot": ServiceConfig(name="robot", base_url="http://localhost:8110", watch_tasks=True),
            "igus": ServiceConfig(name="igus", base_url="http://localhost:8101"),
        },
        status_heartbeat_interval=15.0,
        mqtt_publish_qos=0,
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def _make_bridge(config=None):
    """Create a MqttCommandBridge with all heavy dependencies mocked."""
    cfg = config or _make_config()
    with (
        patch("bridge.get_config_service") as mock_cs,
        patch("bridge.UnifiedMQTTClient") as mock_mqtt_cls,
        patch("bridge.HubAuthManager", return_value=None),
        patch("env_settings.get_env_settings") as mock_env,
    ):
        mock_cs.return_value.get_config.return_value = cfg
        mock_cs.return_value.subscribe = MagicMock()
        mock_env.return_value.parse_long_operations.return_value = None

        mqtt_inst = MagicMock()
        mqtt_inst.is_connected = True
        mqtt_inst.publish.return_value = True
        mock_mqtt_cls.return_value = mqtt_inst

        from bridge import MqttCommandBridge
        bridge = MqttCommandBridge(config=cfg)
        bridge.mqtt_client = mqtt_inst
    return bridge


# ---- _handle_command_message tests ----

class TestHandleCommandMessage:
    def test_invalid_payload_publishes_error(self):
        bridge = _make_bridge()
        bridge._publish_command_error = MagicMock()
        bridge._handle_command_message("navigateTo", "not-a-dict")
        bridge._publish_command_error.assert_called_once()

    def test_missing_command_id_publishes_error(self):
        bridge = _make_bridge()
        bridge._publish_command_error = MagicMock()
        bridge._handle_command_message("navigateTo", {"target_id": "A"})
        bridge._publish_command_error.assert_called_once()
        args = bridge._publish_command_error.call_args[0]
        assert args[1] is None  # command_id is None

    def test_cached_response_replayed(self):
        bridge = _make_bridge()
        cached = {"service": "robot", "success": True, "request_id": "r1"}
        bridge._dedup.get = MagicMock(return_value=cached)
        bridge._send_response = MagicMock()
        bridge._publish_navigation_status = MagicMock()
        bridge._handle_command_message("navigateTo", {"command_id": "c1"})
        bridge._send_response.assert_called_once_with("robot", cached)
        bridge._publish_navigation_status.assert_called_once()

    def test_duplicate_in_progress_ignored(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=False)
        bridge._handle_navigate_command = MagicMock()
        bridge._handle_command_message("navigateTo", {"command_id": "c1"})
        bridge._handle_navigate_command.assert_not_called()

    def test_unsupported_command_publishes_error(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=True)
        bridge._publish_command_error = MagicMock()
        bridge._finish_command = MagicMock()
        bridge._handle_command_message("fly", {"command_id": "c1"})
        bridge._publish_command_error.assert_called_once()
        assert "fly" in bridge._publish_command_error.call_args[0][2]
        bridge._finish_command.assert_called_once_with("c1")

    def test_navigate_routes_correctly(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=True)
        bridge._handle_navigate_command = MagicMock()
        bridge._validate_timestamp = MagicMock(return_value="2024-01-01T00:00:00Z")
        bridge._handle_command_message("navigateTo", {"command_id": "c1", "timestamp": "2024-01-01T00:00:00Z"})
        bridge._handle_navigate_command.assert_called_once()

    def test_cancel_routes_correctly(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=True)
        bridge._handle_cancel_command = MagicMock()
        bridge._validate_timestamp = MagicMock(return_value="2024-01-01T00:00:00Z")
        bridge._handle_command_message("cancel", {"command_id": "c1", "timestamp": "2024-01-01T00:00:00Z"})
        bridge._handle_cancel_command.assert_called_once()

    def test_estop_routes_correctly(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=True)
        bridge._handle_estop_command = MagicMock()
        bridge._handle_command_message("estop", {"command_id": "c1"})
        bridge._handle_estop_command.assert_called_once()

    def test_exception_releases_command_lock(self):
        bridge = _make_bridge()
        bridge._dedup.get = MagicMock(return_value=None)
        bridge._dedup.try_start = MagicMock(return_value=True)
        bridge._validate_timestamp = MagicMock(return_value=None)
        bridge._handle_navigate_command = MagicMock(side_effect=RuntimeError("boom"))
        bridge._finish_command = MagicMock()
        with pytest.raises(RuntimeError):
            bridge._handle_command_message("navigateTo", {"command_id": "c1"})
        bridge._finish_command.assert_called_with("c1")


# ---- _handle_config_message / _handle_broker_config_change tests ----

class TestHandleConfigMessage:
    def test_non_dict_payload_ignored(self):
        bridge = _make_bridge()
        bridge._handle_broker_config_change = MagicMock()
        bridge._handle_config_message("MQTT_BROKER", "not-a-dict")
        bridge._handle_broker_config_change.assert_not_called()

    def test_mqtt_broker_key_routes(self):
        bridge = _make_bridge()
        bridge._handle_broker_config_change = MagicMock()
        bridge._handle_config_message("MQTT_BROKER", {"MQTT_BROKER": "new-host"})
        bridge._handle_broker_config_change.assert_called_once()

    def test_unknown_key_logged(self):
        bridge = _make_bridge()
        bridge._handle_config_message("unknown_key", {"foo": "bar"})
        # Should not raise


class TestHandleBrokerConfigChange:
    def test_no_params_returns_error(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge._handle_broker_config_change({"request_id": "r1"})
        bridge._publish_config_response.assert_called_once()
        call_kw = bridge._publish_config_response.call_args
        # may be called as positional or keyword
        assert call_kw[1].get("success") is False or call_kw[0][1] is False

    def test_invalid_port_string(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge._handle_broker_config_change({"request_id": "r1", "MQTT_PORT": "abc"})
        bridge._publish_config_response.assert_called_once()
        assert "integer" in str(bridge._publish_config_response.call_args)

    def test_port_out_of_range(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge._handle_broker_config_change({"request_id": "r1", "MQTT_PORT": 99999})
        bridge._publish_config_response.assert_called_once()
        assert "65535" in str(bridge._publish_config_response.call_args)

    def test_successful_update(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge.config_service.update_config = MagicMock(return_value=True)
        bridge._handle_broker_config_change({"request_id": "r1", "MQTT_BROKER": "new-host"})
        bridge.config_service.update_config.assert_called_once()
        bridge._publish_config_response.assert_called_with("r1", success=True)

    def test_failed_update(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge.config_service.update_config = MagicMock(return_value=False)
        bridge._handle_broker_config_change({"request_id": "r1", "MQTT_BROKER": "new-host"})
        bridge._publish_config_response.assert_called_once()
        assert bridge._publish_config_response.call_args[1]["success"] is False

    def test_tls_params_included(self):
        bridge = _make_bridge()
        bridge._publish_config_response = MagicMock()
        bridge.config_service.update_config = MagicMock(return_value=True)
        bridge._handle_broker_config_change({
            "request_id": "r1",
            "mqtt_use_tls": True,
            "mqtt_tls_insecure": False,
        })
        call_args = bridge.config_service.update_config.call_args[0][0]
        assert "MQTT_USE_TLS" in call_args
        assert "MQTT_TLS_INSECURE" in call_args


# ---- _publish_config_response ----

class TestPublishConfigResponse:
    def test_success_response(self):
        bridge = _make_bridge()
        bridge._publish_json = MagicMock(return_value=True)
        bridge._publish_config_response("r1", success=True)
        call_args = bridge._publish_json.call_args
        payload = call_args[0][1]
        assert payload["success"] is True
        assert "message" in payload

    def test_error_response(self):
        bridge = _make_bridge()
        bridge._publish_json = MagicMock(return_value=True)
        bridge._publish_config_response("r1", success=False, error="bad thing")
        payload = bridge._publish_json.call_args[0][1]
        assert payload["error"] == "bad thing"
        assert payload["success"] is False


# ---- _submit_http ----

class TestSubmitHttp:
    def test_shutdown_drops_command(self):
        bridge = _make_bridge()
        bridge._shutdown.set()
        bridge._finish_command = MagicMock()
        bridge._submit_http("robot", context={"command_id": "c1"})
        bridge._finish_command.assert_called_with("c1")

    def test_executor_runtime_error(self):
        bridge = _make_bridge()
        bridge._finish_command = MagicMock()
        bridge._http_executor.submit = MagicMock(side_effect=RuntimeError("shut down"))
        bridge._submit_http("robot", context={"command_id": "c2"})
        bridge._finish_command.assert_called_with("c2")

    def test_normal_submit(self):
        bridge = _make_bridge()
        bridge._http_executor.submit = MagicMock()
        bridge._submit_http("robot", context={"command_id": "c3"}, request_id="r1", method="GET", url="/", headers={}, body=None)
        bridge._http_executor.submit.assert_called_once()


# ---- _execute_http_command ----

class TestExecuteHttpCommand:
    def _mock_response(self, ok=True, status_code=200, json_data=None, headers=None):
        resp = MagicMock()
        resp.ok = ok
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.headers = headers or {"content-type": "application/json"}
        resp.text = json.dumps(json_data or {})
        return resp

    def test_successful_request(self):
        bridge = _make_bridge()
        resp = self._mock_response(ok=True, status_code=200, json_data={"result": "ok"})
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.return_value = resp
        bridge._send_response = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="GET",
            url="http://localhost:8110/status", headers={}, body=None,
            context={"command_id": "c1"},
        )
        bridge._send_response.assert_called_once()
        payload = bridge._send_response.call_args[0][1]
        assert payload["success"] is True
        assert payload["status_code"] == 200

    def test_http_error_response(self):
        bridge = _make_bridge()
        resp = self._mock_response(ok=False, status_code=500, json_data={"detail": "error"})
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.return_value = resp
        bridge._send_response = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="POST",
            url="http://localhost:8110/move", headers={}, body={"x": 1},
            context={"command_id": "c1"},
        )
        payload = bridge._send_response.call_args[0][1]
        assert payload["success"] is False
        assert payload["error"]["type"] == "http_error"

    def test_request_exception_publishes_error(self):
        bridge = _make_bridge()
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.side_effect = requests.ConnectionError("refused")
        bridge._publish_http_error_response = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="GET",
            url="http://localhost:8110/status", headers={}, body=None,
            context={"command_id": "c1"},
        )
        bridge._publish_http_error_response.assert_called_once()
        bridge._finish_command.assert_called_with("c1")

    def test_task_id_triggers_watcher(self):
        bridge = _make_bridge()
        resp = self._mock_response(ok=True, status_code=200, json_data={"task_id": "t1"})
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.return_value = resp
        bridge._send_response = MagicMock()
        bridge._start_task_watcher = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="POST",
            url="http://localhost:8110/move", headers={}, body={},
            context={"command_id": "c1", "metadata": {"target_id": "A"}},
        )
        bridge._start_task_watcher.assert_called_once()

    def test_navigation_status_published_on_success(self):
        bridge = _make_bridge()
        resp = self._mock_response(ok=True, status_code=200)
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.return_value = resp
        bridge._send_response = MagicMock()
        bridge._publish_navigation_status = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="POST",
            url="http://localhost:8110/move", headers={}, body={},
            context={"command_id": "c1", "publish_navigation": True, "status_type": "navigation"},
        )
        bridge._publish_navigation_status.assert_called_once()

    def test_json_parse_failure_uses_text(self):
        bridge = _make_bridge()
        resp = self._mock_response(ok=True, status_code=200)
        resp.json.side_effect = ValueError("bad json")
        resp.text = "plain text body"
        bridge._get_http_session = MagicMock()
        bridge._get_http_session().request.return_value = resp
        bridge._send_response = MagicMock()
        bridge._finish_command = MagicMock()

        bridge._execute_http_command(
            service="robot", request_id="r1", method="GET",
            url="http://localhost:8110/status", headers={}, body=None,
        )
        payload = bridge._send_response.call_args[0][1]
        assert payload["body"] == "plain text body"


# ---- _validate_timestamp ----

class TestValidateTimestamp:
    def test_none_returns_none(self):
        bridge = _make_bridge()
        assert bridge._validate_timestamp(None, "navigateTo", "c1") is None

    def test_non_string_returns_none(self):
        bridge = _make_bridge()
        assert bridge._validate_timestamp(12345, "navigateTo", "c1") is None

    def test_valid_iso_returns_timestamp(self):
        bridge = _make_bridge()
        ts = "2024-06-15T10:30:00Z"
        assert bridge._validate_timestamp(ts, "navigateTo", "c1") == ts

    def test_invalid_format_returns_none(self):
        bridge = _make_bridge()
        assert bridge._validate_timestamp("not-a-date", "cancel", "c1") is None


# ---- Heartbeat ----

class TestHeartbeat:
    def test_start_heartbeat_skips_zero_interval(self):
        cfg = _make_config(status_heartbeat_interval=0)
        bridge = _make_bridge(cfg)
        bridge._start_heartbeat()
        assert bridge._heartbeat_thread is None

    def test_start_heartbeat_creates_thread(self):
        cfg = _make_config(status_heartbeat_interval=1.0)
        bridge = _make_bridge(cfg)
        bridge._publish_system_status = MagicMock()
        bridge._publish_connection_status = MagicMock()
        bridge._start_heartbeat()
        assert bridge._heartbeat_thread is not None
        assert bridge._heartbeat_thread.is_alive()
        bridge._stop_heartbeat()

    def test_stop_heartbeat(self):
        cfg = _make_config(status_heartbeat_interval=1.0)
        bridge = _make_bridge(cfg)
        bridge._publish_system_status = MagicMock()
        bridge._publish_connection_status = MagicMock()
        bridge._start_heartbeat()
        bridge._stop_heartbeat()
        assert bridge._heartbeat_thread is None


# ---- Publishing helpers ----

class TestPublishingHelpers:
    def test_publish_system_status(self):
        bridge = _make_bridge()
        bridge._publish_status = MagicMock()
        bridge._publish_system_status()
        bridge._publish_status.assert_called_once()
        payload = bridge._publish_status.call_args[0][1]
        assert payload["type"] == "status"
        assert payload["status_type"] == "system"
        assert "bridge" in payload

    def test_publish_connection_status(self):
        bridge = _make_bridge()
        bridge._publish_status = MagicMock()
        bridge._publish_connection_status()
        payload = bridge._publish_status.call_args[0][1]
        assert payload["status_type"] == "connection"
        assert "mqtt" in payload

    def test_publish_navigation_status_skips_non_navigation(self):
        bridge = _make_bridge()
        bridge._publish_status = MagicMock()
        bridge._publish_navigation_status("ack", True, {}, {"status_type": "system"})
        bridge._publish_status.assert_not_called()

    def test_publish_navigation_status_skips_no_command_id(self):
        bridge = _make_bridge()
        bridge._publish_status = MagicMock()
        bridge._publish_navigation_status("ack", True, {}, {"status_type": "navigation"})
        bridge._publish_status.assert_not_called()

    def test_publish_navigation_status_ok(self):
        bridge = _make_bridge()
        bridge._publish_status = MagicMock()
        ctx = {"status_type": "navigation", "command_id": "c1", "command_name": "navigateTo", "target_id": "A"}
        bridge._publish_navigation_status("acknowledged", True, {}, ctx)
        bridge._publish_status.assert_called_once()

    def test_publish_unknown_service_response(self):
        bridge = _make_bridge()
        bridge._send_response = MagicMock()
        bridge._publish_unknown_service_response("bad_svc", "r1")
        payload = bridge._send_response.call_args[0][1]
        assert payload["success"] is False
        assert payload["status_code"] == 400

    def test_publish_invalid_json_response(self):
        bridge = _make_bridge()
        bridge._send_response = MagicMock()
        bridge._publish_invalid_json_response("robot")
        payload = bridge._send_response.call_args[0][1]
        assert payload["error"]["type"] == "invalid_json"

    def test_publish_command_error(self):
        bridge = _make_bridge()
        bridge._publish_error_ack = MagicMock()
        bridge._publish_navigation_status = MagicMock()
        bridge._publish_command_error("navigateTo", "c1", "bad command")
        bridge._publish_error_ack.assert_called_once()

    def test_publish_http_error_response(self):
        bridge = _make_bridge()
        bridge._publish_error_ack = MagicMock()
        bridge._publish_http_error_response("robot", "r1", "connection refused")
        bridge._publish_error_ack.assert_called_once()


# ---- _publish_json ----

class TestPublishJson:
    def test_not_connected_returns_false(self):
        bridge = _make_bridge()
        type(bridge.mqtt_client).is_connected = PropertyMock(return_value=False)
        result = bridge._publish_json("topic/test", {"a": 1})
        assert result is False

    def test_successful_publish(self):
        bridge = _make_bridge()
        type(bridge.mqtt_client).is_connected = PropertyMock(return_value=True)
        bridge.mqtt_client.publish.return_value = True
        result = bridge._publish_json("topic/test", {"a": 1})
        assert result is True

    def test_exception_returns_false(self):
        bridge = _make_bridge()
        type(bridge.mqtt_client).is_connected = PropertyMock(return_value=True)
        bridge.mqtt_client.publish.side_effect = Exception("boom")
        result = bridge._publish_json("topic/test", {"a": 1})
        assert result is False


# ---- _send_response ----

class TestSendResponse:
    def test_connected_sends_immediately(self):
        bridge = _make_bridge()
        type(bridge.mqtt_client).is_connected = PropertyMock(return_value=True)
        bridge._publish_json = MagicMock(return_value=True)
        bridge._send_response("robot", {"request_id": "r1"})
        bridge._publish_json.assert_called_once()

    def test_disconnected_queues_result(self):
        bridge = _make_bridge()
        type(bridge.mqtt_client).is_connected = PropertyMock(return_value=False)
        bridge._add_task_result_to_queue = MagicMock()
        bridge._send_response("robot", {"request_id": "r1"})
        bridge._add_task_result_to_queue.assert_called_once_with("r1", {"request_id": "r1"})


# ---- Queue flushing ----

class TestQueueFlushing:
    def test_add_task_result_ignores_empty_request_id(self):
        bridge = _make_bridge()
        bridge._add_task_result_to_queue("", {"data": 1})
        assert len(bridge._task_result_queue) == 0

    def test_add_and_flush(self):
        bridge = _make_bridge()
        bridge._add_task_result_to_queue("r1", {"service": "robot", "data": 1})
        assert "r1" in bridge._task_result_queue
        bridge._send_response = MagicMock()
        bridge._flush_task_result_queue()
        bridge._send_response.assert_called_once()
        assert len(bridge._task_result_queue) == 0

    def test_flush_skips_when_already_flushing(self):
        bridge = _make_bridge()
        bridge._flushing_queue = True
        bridge._task_result_queue["r1"] = {"service": "robot"}
        bridge._send_response = MagicMock()
        bridge._flush_task_result_queue()
        bridge._send_response.assert_not_called()

    def test_flush_handles_send_error(self):
        bridge = _make_bridge()
        bridge._task_result_queue["r1"] = {"service": "robot"}
        bridge._send_response = MagicMock(side_effect=Exception("send failed"))
        bridge._flush_task_result_queue()
        # Should not raise, item stays in queue
        assert "r1" in bridge._task_result_queue


# ---- Error publishing ----

class TestErrorPublishing:
    def test_publish_processing_error_with_service(self):
        bridge = _make_bridge()
        bridge._send_response = MagicMock()
        bridge._publish_processing_error("aroc/robot/r1/cmd/robot/test", "oops")
        bridge._send_response.assert_called_once()

    def test_publish_processing_error_command_topic(self):
        bridge = _make_bridge()
        bridge._publish_json = MagicMock(return_value=True)
        # topic with /commands/ but no extractable service (< 5 parts)
        bridge._publish_processing_error("aroc/commands/test", "oops")
        bridge._publish_json.assert_called_once()

    def test_publish_json_parse_error_with_service(self):
        bridge = _make_bridge()
        bridge._publish_invalid_json_response = MagicMock()
        bridge._publish_json_parse_error("aroc/robot/r1/cmd/robot/test", "bad json")
        bridge._publish_invalid_json_response.assert_called_once()

    def test_publish_json_parse_error_no_service(self):
        bridge = _make_bridge()
        bridge._publish_json = MagicMock(return_value=True)
        bridge._publish_json_parse_error("short/topic", "bad json")
        bridge._publish_json.assert_called_once()


# ---- Helper methods ----

class TestHelperMethods:
    def test_extract_service(self):
        bridge = _make_bridge()
        assert bridge._extract_service("aroc/robot/r1/cmd/robot") == "robot"
        assert bridge._extract_service("short") is None

    def test_extract_command(self):
        bridge = _make_bridge()
        assert bridge._extract_command("aroc/robot/r1/commands/navigateTo") == "navigateTo"
        assert bridge._extract_command("aroc/robot/r1/cmd/robot") is None
        assert bridge._extract_command("short") is None

    def test_extract_config_key(self):
        bridge = _make_bridge()
        assert bridge._extract_config_key("aroc/robot/r1/config/MQTT_BROKER") == "MQTT_BROKER"
        assert bridge._extract_config_key("aroc/robot/r1/cmd/robot") is None
        assert bridge._extract_config_key("short") is None

    def test_effective_robot_id_no_auth(self):
        bridge = _make_bridge()
        bridge._auth_manager = None
        assert bridge._effective_robot_id() == "test-robot"

    def test_effective_robot_id_with_auth(self):
        bridge = _make_bridge()
        auth = MagicMock()
        auth.robot_id.return_value = "auth-robot"
        bridge._auth_manager = auth
        assert bridge._effective_robot_id() == "auth-robot"

    def test_format_timestamp_none(self):
        bridge = _make_bridge()
        assert bridge._format_timestamp(None) is None

    def test_format_timestamp_valid(self):
        bridge = _make_bridge()
        result = bridge._format_timestamp(0.0)
        assert "1970" in result

    def test_now_iso(self):
        bridge = _make_bridge()
        result = bridge._now_iso()
        assert "T" in result

    def test_prepare_request_headers_none_filtered(self):
        bridge = _make_bridge()
        bridge._auth_manager = None
        result = bridge._prepare_request_headers({"X-Custom": "val", "Remove": None})
        assert result["X-Custom"] == "val"
        assert "Remove" not in result

    def test_auth_headers_with_manager(self):
        bridge = _make_bridge()
        auth = MagicMock()
        auth.auth_headers.return_value = {"Authorization": "Bearer token"}
        bridge._auth_manager = auth
        assert bridge._auth_headers() == {"Authorization": "Bearer token"}

    def test_auth_headers_without_manager(self):
        bridge = _make_bridge()
        bridge._auth_manager = None
        assert bridge._auth_headers() == {}


# ---- _handle_incoming_message service routing ----

class TestIncomingMessageRouting:
    def test_unknown_service_publishes_error(self):
        bridge = _make_bridge()
        bridge._publish_unknown_service_response = MagicMock()
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/unknown_svc", payload)
        bridge._publish_unknown_service_response.assert_called_once()

    def test_invalid_path_rejected(self):
        bridge = _make_bridge()
        bridge._publish_error_ack = MagicMock()
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/foo\x00bar"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._publish_error_ack.assert_called_once()

    def test_valid_service_submits_http(self):
        bridge = _make_bridge()
        bridge._submit_http = MagicMock()
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/status"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._submit_http.assert_called_once()

    def test_invalid_headers_returns_error(self):
        bridge = _make_bridge()
        bridge._publish_error_ack = MagicMock()
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/", "headers": "not-a-dict"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._publish_error_ack.assert_called_once()

    def test_service_config_missing_publishes_error(self):
        """When _build_http_url returns None, service_config_missing is published."""
        bridge = _make_bridge()
        bridge._build_http_url = MagicMock(return_value=None)
        bridge._publish_service_config_missing = MagicMock()
        payload = json.dumps({"request_id": "r1", "method": "GET", "path": "/status"}).encode()
        bridge._handle_incoming_message("aroc/robot/r1/cmd/robot", payload)
        bridge._publish_service_config_missing.assert_called_once()
