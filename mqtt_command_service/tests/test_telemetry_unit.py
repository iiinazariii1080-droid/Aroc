"""Tests for telemetry.py — thread-local session and service status."""
import threading
from unittest.mock import Mock, patch

import telemetry


class TestThreadLocalSession:
    def test_same_thread_returns_same_session(self):
        """Same thread gets the same Session object."""
        s1 = telemetry._get_http_session()
        s2 = telemetry._get_http_session()
        assert s1 is s2

    def test_different_threads_get_different_sessions(self):
        """Different threads get different Session objects."""
        sessions = {}

        def worker(name):
            # Store the actual object to keep it alive (prevent id reuse)
            sessions[name] = telemetry._get_http_session()

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t1.join()
        t2.start()
        t2.join()
        assert sessions["a"] is not sessions["b"]


class TestFetchServiceStatus:
    def test_online_service(self):
        """200 response with JSON returns online status."""
        with patch("telemetry._get_http_session") as mock_get:
            mock_session = mock_get.return_value
            mock_resp = mock_session.get.return_value
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            status, data, err = telemetry.fetch_service_status("robot", "http://localhost/status")
            assert status == "online"
            assert data == {"status": "ok"}
            assert err is None

    def test_http_error(self):
        """Non-200 returns error status."""
        with patch("telemetry._get_http_session") as mock_get:
            mock_session = mock_get.return_value
            mock_resp = mock_session.get.return_value
            mock_resp.status_code = 500
            status, data, err = telemetry.fetch_service_status("robot", "http://localhost/status")
            assert status == "error"
            assert data is None
            assert "500" in err

    def test_connection_error(self):
        """Connection error returns offline status."""
        import requests
        with patch("telemetry._get_http_session") as mock_get:
            mock_session = mock_get.return_value
            mock_session.get.side_effect = requests.exceptions.ConnectionError("connection refused")
            status, _data, _err = telemetry.fetch_service_status("robot", "http://localhost/status")
            assert status == "offline"


class TestBuildStatusPayload:
    def test_payload_structure(self):
        """build_status_payload returns correct structure."""
        payload = telemetry.build_status_payload("test-robot", "robot", "online", {"key": "val"}, None)
        assert payload["robot_id"] == "test-robot"
        assert payload["service"] == "robot"
        assert payload["status"] == "online"
        assert payload["data"] == {"key": "val"}
        assert payload["error"] is None
        assert "timestamp" in payload


class TestGetRobotId:
    def test_returns_from_config_service(self):
        """Prefers ConfigService robot_id."""
        mock_cfg = Mock()
        mock_cfg.robot_id = "cfg-robot"
        mock_svc = Mock()
        mock_svc.get_config.return_value = mock_cfg
        with patch("telemetry.get_config_service", return_value=mock_svc):
            assert telemetry.get_robot_id() == "cfg-robot"

    def test_fallback_to_env(self):
        """Falls back to env var when services unavailable."""
        from env_settings import reset_env_settings
        with patch("telemetry.get_config_service", side_effect=Exception("no svc")), \
             patch("telemetry.CONFIG_AVAILABLE", False), \
             patch.dict("os.environ", {"ROBOT_ID": "env-robot"}):
            reset_env_settings()
            try:
                assert telemetry.get_robot_id() == "env-robot"
            finally:
                reset_env_settings()
