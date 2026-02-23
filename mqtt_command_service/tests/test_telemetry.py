"""Tests for telemetry.py — MQTT setup, service polling, publishing,
WebRTC checks, helpers, and the telemetry loop.

Targets ~125 missed statements (53% → higher).
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import requests

# ---- _compact ----

class TestCompact:
    def test_short_dict(self):
        from telemetry import _compact
        result = _compact({"a": 1})
        assert result == '{"a":1}'

    def test_long_truncated(self):
        from telemetry import _compact
        big = {"data": "x" * 1000}
        result = _compact(big, limit=50)
        assert result.endswith("...(truncated)")
        assert len(result) <= 70  # 50 + len of suffix

    def test_non_serialisable_fallback(self):
        from telemetry import _compact
        obj = object()
        result = _compact(obj)
        assert isinstance(result, str)


# ---- fetch_service_status ----

class TestFetchServiceStatus:
    def test_success_json(self):
        from telemetry import fetch_service_status
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            status, data, error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "online"
        assert data == {"status": "ok"}
        assert error is None

    def test_success_non_json(self):
        from telemetry import fetch_service_status
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")
        mock_resp.text = "plain text"
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            status, data, _error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "online"
        assert data == {"raw": "plain text"}

    def test_http_error_status(self):
        from telemetry import fetch_service_status
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            status, _data, error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "error"
        assert "500" in error

    def test_timeout(self):
        from telemetry import fetch_service_status
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.side_effect = requests.exceptions.Timeout()
            status, _data, error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "error"
        assert error == "timeout"

    def test_connection_error(self):
        from telemetry import fetch_service_status
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.side_effect = requests.exceptions.ConnectionError()
            status, _data, error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "offline"
        assert error == "connection_error"

    def test_generic_exception(self):
        from telemetry import fetch_service_status
        with patch("telemetry._get_http_session") as mock_session:
            mock_session.return_value.get.side_effect = RuntimeError("weird")
            status, _data, error = fetch_service_status("robot", "http://localhost:8110/status")
        assert status == "error"
        assert "RuntimeError" in error


# ---- publish_mqtt ----

class TestPublishMqtt:
    def test_no_client_returns_false(self):
        import telemetry
        orig = telemetry.mqtt_client
        try:
            telemetry.mqtt_client = None
            result = telemetry.publish_mqtt("topic", {"a": 1})
            assert result is False
        finally:
            telemetry.mqtt_client = orig

    def test_not_connected_returns_false(self):
        import telemetry
        orig = telemetry.mqtt_client
        try:
            mock_client = MagicMock()
            mock_client.is_connected = False
            telemetry.mqtt_client = mock_client
            result = telemetry.publish_mqtt("topic", {"a": 1})
            assert result is False
        finally:
            telemetry.mqtt_client = orig

    def test_publish_success(self):
        import telemetry
        orig = telemetry.mqtt_client
        try:
            mock_client = MagicMock()
            mock_client.is_connected = True
            mock_client.publish.return_value = True
            telemetry.mqtt_client = mock_client
            result = telemetry.publish_mqtt("topic", {"a": 1})
            assert result is True
            mock_client.publish.assert_called_once()
        finally:
            telemetry.mqtt_client = orig

    def test_oversized_payload_rejected(self):
        import telemetry
        orig = telemetry.mqtt_client
        try:
            mock_client = MagicMock()
            mock_client.is_connected = True
            telemetry.mqtt_client = mock_client
            huge_payload = {"data": "x" * (2 * 1024 * 1024)}  # > 1MB
            result = telemetry.publish_mqtt("topic", huge_payload)
            assert result is False
        finally:
            telemetry.mqtt_client = orig


# ---- setup_mqtt_client / connect_mqtt_blocking ----

class TestMqttSetup:
    def test_setup_creates_client(self):
        import telemetry
        orig_client = telemetry.mqtt_client
        orig_cs = telemetry._config_service
        try:
            telemetry.mqtt_client = None
            telemetry._config_service = None
            with (
                patch("telemetry.get_config_service"),
                patch("telemetry.UnifiedMQTTClient") as mock_mqtt_cls,
                patch("app.services.mqtt_state.register") as mock_register,
            ):
                mock_mqtt_cls.return_value = MagicMock()
                telemetry.setup_mqtt_client()
                assert telemetry.mqtt_client is not None
                mock_register.assert_called_once()
        finally:
            telemetry.mqtt_client = orig_client
            telemetry._config_service = orig_cs

    def test_connect_blocking_success(self):
        import telemetry
        orig_client = telemetry.mqtt_client
        orig_cs = telemetry._config_service
        try:
            mock_client = MagicMock()
            mock_client.is_connected = True
            telemetry.mqtt_client = mock_client
            telemetry._config_service = MagicMock()
            result = telemetry.connect_mqtt_blocking()
            assert result is True
            mock_client.start.assert_called_once()
        finally:
            telemetry.mqtt_client = orig_client
            telemetry._config_service = orig_cs

    def test_connect_blocking_timeout(self):
        import telemetry
        orig_client = telemetry.mqtt_client
        orig_cs = telemetry._config_service
        orig_flag = telemetry.shutdown_flag
        try:
            mock_client = MagicMock()
            mock_client.is_connected = False
            telemetry.mqtt_client = mock_client
            telemetry._config_service = MagicMock()
            telemetry.shutdown_flag = threading.Event()
            telemetry.shutdown_flag.set()  # break early
            result = telemetry.connect_mqtt_blocking()
            assert result is False
        finally:
            telemetry.mqtt_client = orig_client
            telemetry._config_service = orig_cs
            telemetry.shutdown_flag = orig_flag


# ---- check_webrtc_connection ----

class TestCheckWebrtc:
    def test_no_websocket_lib(self):
        import telemetry
        orig = telemetry.WEBSOCKET_AVAILABLE
        try:
            telemetry.WEBSOCKET_AVAILABLE = False
            assert telemetry.check_webrtc_connection() is False
        finally:
            telemetry.WEBSOCKET_AVAILABLE = orig

    def test_cached_result_within_interval(self):
        import telemetry
        orig_available = telemetry.WEBSOCKET_AVAILABLE
        orig_check = telemetry.webrtc_last_check
        try:
            telemetry.WEBSOCKET_AVAILABLE = True
            telemetry.webrtc_last_check = time.time() + 9999  # far future
            telemetry.webrtc_connected.set()
            assert telemetry.check_webrtc_connection() is True
        finally:
            telemetry.WEBSOCKET_AVAILABLE = orig_available
            telemetry.webrtc_last_check = orig_check
            telemetry.webrtc_connected.clear()


# ---- get_robot_id ----

class TestGetRobotId:
    def test_from_config_service(self):
        from telemetry import get_robot_id
        with patch("telemetry.get_config_service") as mock_cs:
            mock_cs.return_value.get_config.return_value.robot_id = "robot-1"
            assert get_robot_id() == "robot-1"

    def test_fallback_on_exception(self):
        from telemetry import get_robot_id
        with (
            patch("telemetry.get_config_service", side_effect=Exception("no service")),
            patch("telemetry.load_bridge_config") as mock_load,
        ):
            mock_load.return_value.robot_id = "robot-2"
            assert get_robot_id() == "robot-2"


# ---- publish_service_status ----

class TestPublishServiceStatus:
    def test_calls_publish_mqtt(self):
        from telemetry import publish_service_status
        with patch("telemetry.publish_mqtt") as mock_pub, patch("telemetry.get_robot_id", return_value="r1"):
            publish_service_status("robot", {"status": "ok"})
            mock_pub.assert_called_once()
            topic = mock_pub.call_args[0][0]
            assert "r1" in topic
            assert "robot" in topic


# ---- handle_signal ----

class TestHandleSignal:
    def test_sets_shutdown_flag(self):
        import telemetry
        orig_flag = telemetry.shutdown_flag
        orig_client = telemetry.mqtt_client
        try:
            telemetry.shutdown_flag = threading.Event()
            mock_client = MagicMock()
            telemetry.mqtt_client = mock_client
            telemetry.handle_signal(2, None)
            assert telemetry.shutdown_flag.is_set()
            mock_client.stop.assert_called_once()
        finally:
            telemetry.shutdown_flag = orig_flag
            telemetry.mqtt_client = orig_client


# ---- _build_services / _get_janus_endpoints ----

class TestServiceBuilders:
    def test_build_services_local(self):
        from telemetry import _build_services
        with patch("telemetry.get_env_settings") as mock_env:
            mock_env.return_value.is_remote = False
            mock_env.return_value.local_ip = "192.168.1.1"
            result = _build_services()
            assert "robot" in result
            assert "192.168.1.1" in result["robot"]

    def test_build_services_remote(self):
        from telemetry import _build_services
        with patch("telemetry.get_env_settings") as mock_env:
            mock_env.return_value.is_remote = True
            mock_env.return_value.remote_address = "remote.host"
            result = _build_services()
            assert "robot" in result
            assert "remote.host" in result["robot"]

    def test_get_janus_endpoints(self):
        from telemetry import _get_janus_endpoints
        with patch("telemetry.get_env_settings") as mock_env:
            mock_env.return_value.effective_janus_ws_depth = "ws://depth"
            mock_env.return_value.effective_janus_ws_color = "ws://color"
            d, c = _get_janus_endpoints()
            assert d == "ws://depth"
            assert c == "ws://color"


# ---- _get_http_session ----

class TestGetHttpSession:
    def test_returns_session(self):
        from telemetry import _get_http_session
        session = _get_http_session()
        assert hasattr(session, "get")

    def test_same_thread_same_session(self):
        from telemetry import _get_http_session
        s1 = _get_http_session()
        s2 = _get_http_session()
        assert s1 is s2


# ---- telemetry_loop (single iteration) ----

class TestTelemetryLoop:
    def test_single_iteration_publishes(self):
        """Run one iteration of telemetry_loop then set shutdown flag."""
        import telemetry

        orig_client = telemetry.mqtt_client
        orig_cs = telemetry._config_service
        orig_flag = telemetry.shutdown_flag

        try:
            # Set up mocks
            mock_client = MagicMock()
            mock_client.is_connected = True
            mock_client.publish.return_value = True
            telemetry.mqtt_client = mock_client
            telemetry._config_service = MagicMock()
            telemetry.shutdown_flag = threading.Event()

            call_count = 0

            def mock_fetch(name, url):
                nonlocal call_count
                call_count += 1
                # After first poll iteration, signal shutdown
                telemetry.shutdown_flag.set()
                return "online", {"status": "ok"}, None

            with (
                patch("telemetry.fetch_service_status", side_effect=mock_fetch),
                patch("telemetry.publish_mqtt", return_value=True) as mock_pub,
                patch("telemetry.check_webrtc_connection", return_value=False),
                patch("telemetry.get_robot_id", return_value="r1"),
                patch("telemetry._build_services", return_value={"robot": "http://localhost:8110/status"}),
                patch("telemetry.build_navigation_status_payload", return_value=None),
                patch("telemetry.build_system_status_payload", return_value=None),
                patch("telemetry.build_connection_status_payload", return_value={"mqtt": True, "webrtc": False}),
                patch("telemetry.build_telemetry_payload", return_value=None),
                patch("telemetry.build_status_payload", return_value={"type": "status"}),
            ):
                telemetry.telemetry_loop()

            assert call_count >= 1
            assert mock_pub.call_count >= 1
        finally:
            telemetry.mqtt_client = orig_client
            telemetry._config_service = orig_cs
            telemetry.shutdown_flag = orig_flag
