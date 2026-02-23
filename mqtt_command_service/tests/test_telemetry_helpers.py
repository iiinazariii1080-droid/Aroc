"""Tests for telemetry.py helper functions (not the main loop)."""

import time
from unittest.mock import MagicMock, patch

import requests

import telemetry


class TestGetRobotId:
    def test_from_config_service(self):
        mock_cfg = MagicMock()
        mock_cfg.robot_id = "cfg-robot"
        mock_svc = MagicMock()
        mock_svc.get_config.return_value = mock_cfg
        with patch("telemetry.get_config_service", return_value=mock_svc):
            assert telemetry.get_robot_id() == "cfg-robot"

    def test_fallback_to_env(self):
        with patch("telemetry.get_config_service", side_effect=Exception("down")):
            with patch("telemetry.CONFIG_AVAILABLE", False):
                with patch("telemetry.get_env_settings") as mock_env:
                    mock_env.return_value.robot_id = "env-robot"
                    assert telemetry.get_robot_id() == "env-robot"


class TestCompact:
    def test_short_json(self):
        result = telemetry._compact({"key": "val"})
        assert '"key"' in result

    def test_truncation(self):
        big = {"data": "x" * 2000}
        result = telemetry._compact(big, limit=100)
        assert result.endswith("...(truncated)")
        assert len(result) < 200

    def test_non_serializable(self):
        result = telemetry._compact(object())
        assert isinstance(result, str)


class TestFetchServiceStatus:
    @patch("telemetry._get_http_session")
    def test_online(self, mock_session_fn):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_session_fn.return_value.get.return_value = mock_resp

        status, data, err = telemetry.fetch_service_status("robot", "http://robot:8080/status")
        assert status == "online"
        assert data == {"status": "ok"}
        assert err is None

    @patch("telemetry._get_http_session")
    def test_non_200(self, mock_session_fn):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_session_fn.return_value.get.return_value = mock_resp

        status, _data, err = telemetry.fetch_service_status("robot", "http://robot:8080/status")
        assert status == "error"
        assert "500" in err

    @patch("telemetry._get_http_session")
    def test_timeout(self, mock_session_fn):
        mock_session_fn.return_value.get.side_effect = requests.exceptions.Timeout()
        status, _data, err = telemetry.fetch_service_status("robot", "http://robot:8080/status")
        assert status == "error"
        assert err == "timeout"

    @patch("telemetry._get_http_session")
    def test_connection_error(self, mock_session_fn):
        mock_session_fn.return_value.get.side_effect = requests.exceptions.ConnectionError()
        status, _data, err = telemetry.fetch_service_status("robot", "http://robot:8080/status")
        assert status == "offline"
        assert err == "connection_error"

    @patch("telemetry._get_http_session")
    def test_json_parse_failure(self, mock_session_fn):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "plain text"
        mock_session_fn.return_value.get.return_value = mock_resp

        status, data, _err = telemetry.fetch_service_status("robot", "http://robot:8080/status")
        assert status == "online"
        assert data == {"raw": "plain text"}


class TestCheckWebrtcConnection:
    @patch("telemetry.WEBSOCKET_AVAILABLE", False)
    def test_no_websocket_lib(self):
        assert telemetry.check_webrtc_connection() is False

    @patch("telemetry.WEBSOCKET_AVAILABLE", True)
    @patch("telemetry._ws_check_interval", return_value=0)
    @patch("telemetry._get_janus_endpoints", return_value=("ws://a:8188", "ws://b:8188"))
    @patch("telemetry.websocket", create=True)
    def test_connection_success(self, mock_ws, mock_endpoints, mock_interval):
        mock_ws.WebSocketTimeoutException = type("WebSocketTimeoutException", (Exception,), {})
        mock_ws.WebSocketConnectionClosedException = type("WebSocketConnectionClosedException", (Exception,), {})
        mock_conn = MagicMock()
        mock_ws.create_connection.return_value = mock_conn
        telemetry.webrtc_last_check = 0  # force check
        result = telemetry.check_webrtc_connection()
        assert result is True
        mock_conn.close.assert_called_once()

    @patch("telemetry.WEBSOCKET_AVAILABLE", True)
    @patch("telemetry._ws_check_interval", return_value=0)
    @patch("telemetry._get_janus_endpoints", return_value=("ws://a:8188", "ws://b:8188"))
    @patch("telemetry.websocket", create=True)
    def test_connection_failure(self, mock_ws, mock_endpoints, mock_interval):
        mock_ws.WebSocketTimeoutException = type("WebSocketTimeoutException", (Exception,), {})
        mock_ws.WebSocketConnectionClosedException = type("WebSocketConnectionClosedException", (Exception,), {})
        mock_ws.create_connection.side_effect = Exception("refused")
        telemetry.webrtc_last_check = 0
        result = telemetry.check_webrtc_connection()
        assert result is False

    @patch("telemetry.WEBSOCKET_AVAILABLE", True)
    @patch("telemetry._ws_check_interval", return_value=3600)
    def test_cached_result(self, mock_interval):
        """Uses cached result when within check interval."""
        telemetry.webrtc_last_check = time.time()
        telemetry.webrtc_connected.set()
        result = telemetry.check_webrtc_connection()
        assert result is True


class TestPublishMqtt:
    def test_no_client(self):
        original = telemetry.mqtt_client
        telemetry.mqtt_client = None
        try:
            assert telemetry.publish_mqtt("topic", {}) is False
        finally:
            telemetry.mqtt_client = original

    def test_not_connected(self):
        original = telemetry.mqtt_client
        mock_client = MagicMock()
        mock_client.is_connected = False
        telemetry.mqtt_client = mock_client
        try:
            assert telemetry.publish_mqtt("topic", {}) is False
        finally:
            telemetry.mqtt_client = original

    def test_publish_success(self):
        original = telemetry.mqtt_client
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.publish.return_value = True
        telemetry.mqtt_client = mock_client
        try:
            assert telemetry.publish_mqtt("topic", {"data": 1}) is True
            mock_client.publish.assert_called_once()
        finally:
            telemetry.mqtt_client = original

    def test_payload_too_large(self):
        original = telemetry.mqtt_client
        mock_client = MagicMock()
        mock_client.is_connected = True
        telemetry.mqtt_client = mock_client
        try:
            huge = {"data": "x" * 2_000_000}  # Over 1MB
            assert telemetry.publish_mqtt("topic", huge) is False
        finally:
            telemetry.mqtt_client = original


class TestSetupMqttClient:
    @patch("telemetry.get_config_service")
    @patch("telemetry.UnifiedMQTTClient")
    @patch("app.services.mqtt_state.register")
    def test_setup(self, mock_register, mock_umc, mock_svc):
        original_client = telemetry.mqtt_client
        original_cfg = telemetry._config_service
        telemetry.mqtt_client = None
        telemetry._config_service = None
        try:
            telemetry.setup_mqtt_client()
            mock_umc.assert_called_once()
            mock_register.assert_called_once_with("telemetry", mock_umc.return_value)
        finally:
            telemetry.mqtt_client = original_client
            telemetry._config_service = original_cfg


class TestConnectMqttBlocking:
    @patch("telemetry.setup_mqtt_client")
    def test_connect_success(self, mock_setup):
        original = telemetry.mqtt_client
        mock_client = MagicMock()
        mock_client.is_connected = True
        telemetry.mqtt_client = mock_client
        try:
            assert telemetry.connect_mqtt_blocking() is True
            mock_client.start.assert_called_once()
        finally:
            telemetry.mqtt_client = original

    @patch("telemetry.setup_mqtt_client")
    def test_connect_timeout(self, mock_setup):
        original = telemetry.mqtt_client
        mock_client = MagicMock()
        mock_client.is_connected = False
        telemetry.mqtt_client = mock_client
        telemetry.shutdown_flag.set()
        try:
            assert telemetry.connect_mqtt_blocking() is False
        finally:
            telemetry.mqtt_client = original
            telemetry.shutdown_flag.clear()


class TestPublishServiceStatus:
    @patch("telemetry.publish_mqtt")
    @patch("telemetry.get_robot_id", return_value="r-1")
    def test_publishes_correct_topic(self, _, mock_pub):
        telemetry.publish_service_status("robot", {"status": "online"})
        topic = mock_pub.call_args[0][0]
        assert topic == "aroc/robot/r-1/status/robot"


class TestHelperFunctions:
    def test_poll_interval(self):
        with patch("telemetry.get_env_settings") as mock:
            mock.return_value.poll_interval_seconds = 5
            assert telemetry._poll_interval() == 5

    def test_http_timeout(self):
        with patch("telemetry.get_env_settings") as mock:
            mock.return_value.telemetry_http_timeout = 10
            assert telemetry._http_timeout() == 10

    def test_ws_check_timeout(self):
        with patch("telemetry.get_env_settings") as mock:
            mock.return_value.websocket_check_timeout = 2.5
            assert telemetry._ws_check_timeout() == 2.5

    def test_ws_check_interval(self):
        with patch("telemetry.get_env_settings") as mock:
            mock.return_value.websocket_check_interval = 30.0
            assert telemetry._ws_check_interval() == 30.0
