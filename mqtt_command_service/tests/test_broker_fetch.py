"""Tests for shared.broker_fetch — config fetcher with retry/backoff."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from shared.broker_fetch import build_mqtt_connection_config, fetch_config


class TestFetchConfig:
    def test_success_on_first_attempt(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"broker": "test.mqtt.com", "broker_port": 1883}
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True

        mock_pw_response = MagicMock()
        mock_pw_response.ok = True
        mock_pw_response.json.return_value = {"mqtt_password": "secret"}

        def build(data):
            return data

        with patch("shared.broker_fetch.requests.get") as mock_get:
            mock_get.side_effect = [mock_response, mock_pw_response]
            result = fetch_config("http://config-api:8100", build)

        assert result["broker"] == "test.mqtt.com"
        assert result["mqtt_password"] == "secret"

    def test_retries_on_failure(self):
        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = requests.HTTPError("503")

        mock_success = MagicMock()
        mock_success.json.return_value = {"broker": "ok.mqtt.com"}
        mock_success.raise_for_status = MagicMock()
        mock_success.ok = True

        mock_pw = MagicMock()
        mock_pw.ok = True
        mock_pw.json.return_value = {"mqtt_password": "pw"}

        broker_call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal broker_call_count
            url = args[0]
            if "/password" in url:
                return mock_pw
            broker_call_count += 1
            if broker_call_count <= 2:
                return mock_fail
            return mock_success

        with patch("shared.broker_fetch.requests.get", side_effect=side_effect):
            with patch("shared.retry.time.sleep"):
                result = fetch_config(
                    "http://config-api:8100",
                    lambda d: d,
                    max_retries=5,
                    initial_delay=0.01,
                )

        assert result["broker"] == "ok.mqtt.com"

    def test_raises_after_max_retries(self):
        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = requests.HTTPError("503")

        with patch("shared.broker_fetch.requests.get", return_value=mock_fail):
            with patch("shared.retry.time.sleep"):
                with patch("shared.broker_fetch._load_config_cache", return_value=None):
                    with pytest.raises(RuntimeError, match="Cannot fetch broker config"):
                        fetch_config(
                            "http://config-api:8100",
                            lambda d: d,
                            max_retries=2,
                            initial_delay=0.01,
                        )

    def test_password_fetch_failure_retries(self):
        """Password fetch failure is treated as a retryable error."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"broker": "test.mqtt.com"}
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True

        pw_attempt = 0

        def side_effect(*args, **kwargs):
            nonlocal pw_attempt
            url = args[0]
            if "/password" in url:
                pw_attempt += 1
                if pw_attempt <= 1:
                    raise requests.ConnectionError("refused")
                pw_ok = MagicMock()
                pw_ok.ok = True
                pw_ok.json.return_value = {"mqtt_password": "recovered"}
                return pw_ok
            return mock_response

        with patch("shared.broker_fetch.requests.get", side_effect=side_effect):
            with patch("shared.retry.time.sleep"):
                result = fetch_config(
                    "http://config-api:8100",
                    lambda d: d,
                    max_retries=3,
                    initial_delay=0.01,
                )

        assert result["broker"] == "test.mqtt.com"
        assert result["mqtt_password"] == "recovered"


class TestBuildMqttConnectionConfig:
    def test_basic_config(self):
        data = {
            "broker": "mqtt.example.com",
            "broker_port": 1883,
            "mqtt_user": "user1",
            "mqtt_password": "pass1",
            "mqtt_use_tls": False,
            "mqtt_tls_insecure": False,
        }
        cfg = build_mqtt_connection_config(data, "robot-01", "client-01", 1)
        assert cfg.broker == "mqtt.example.com"
        assert cfg.broker_port == 1883
        assert cfg.mqtt_user == "user1"
        assert cfg.mqtt_password == "pass1"
        assert cfg.robot_id == "robot-01"
        assert cfg.client_id == "client-01"
        assert cfg.mqtt_publish_qos == 1
        assert cfg.mqtt_use_tls is False

    def test_tls_auto_port(self):
        data = {
            "broker": "mqtt.example.com",
            "broker_port": 1883,  # default non-TLS port
            "mqtt_use_tls": True,
        }
        cfg = build_mqtt_connection_config(data, "robot-01", "client-01", 1)
        assert cfg.broker_port == 8883  # auto-switched to TLS port
        assert cfg.mqtt_use_tls is True

    def test_qos_clamped(self):
        data = {"broker": "x", "broker_port": 1883}
        cfg = build_mqtt_connection_config(data, "r", "c", 5)
        assert cfg.mqtt_publish_qos == 2  # clamped to max

        cfg2 = build_mqtt_connection_config(data, "r", "c", -1)
        assert cfg2.mqtt_publish_qos == 0  # clamped to min

    def test_defaults_for_missing_fields(self):
        data = {}
        cfg = build_mqtt_connection_config(data, "r", "c", 1)
        assert cfg.broker == ""
        assert cfg.mqtt_user == ""
        assert cfg.mqtt_password == ""
        assert cfg.mqtt_use_tls is False
        assert cfg.mqtt_tls_insecure is False
