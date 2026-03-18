"""Unit tests for config.py — env parsing, broker config building, settings."""

import logging
import os
from unittest.mock import patch

import pytest

from config import (
    TelemetryServiceSettings,
    fetch_mqtt_connection_config,
    get_settings,
)
from shared.env import env_bool, env_float, env_int, validate_robot_id

# ------------------------------------------------------------------
# env_float
# ------------------------------------------------------------------


class TestEnvFloat:
    def test_returns_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert env_float("MISSING_KEY", 1.5) == 1.5

    def test_parses_valid_float(self):
        with patch.dict(os.environ, {"MY_FLOAT": "3.14"}):
            assert env_float("MY_FLOAT", 0.0) == 3.14

    def test_parses_integer_string(self):
        with patch.dict(os.environ, {"MY_FLOAT": "42"}):
            assert env_float("MY_FLOAT", 0.0) == 42.0

    def test_raises_on_invalid(self):
        with patch.dict(os.environ, {"MY_FLOAT": "not_a_number"}):
            with pytest.raises(ValueError, match="Invalid float value"):
                env_float("MY_FLOAT", 0.0)


# ------------------------------------------------------------------
# env_int
# ------------------------------------------------------------------


class TestEnvInt:
    def test_returns_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert env_int("MISSING_KEY", 10) == 10

    def test_parses_valid_int(self):
        with patch.dict(os.environ, {"MY_INT": "7"}):
            assert env_int("MY_INT", 0) == 7

    def test_raises_on_float_string(self):
        with patch.dict(os.environ, {"MY_INT": "3.5"}):
            with pytest.raises(ValueError, match="Invalid int value"):
                env_int("MY_INT", 0)

    def test_raises_on_invalid(self):
        with patch.dict(os.environ, {"MY_INT": "abc"}):
            with pytest.raises(ValueError, match="Invalid int value"):
                env_int("MY_INT", 0)


# ------------------------------------------------------------------
# env_bool
# ------------------------------------------------------------------


class TestEnvBool:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "on", "YES", "ON"])
    def test_truthy_values(self, value):
        with patch.dict(os.environ, {"MY_BOOL": value}):
            assert env_bool("MY_BOOL", False) is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "off", "NO", "OFF", ""])
    def test_falsy_values(self, value):
        with patch.dict(os.environ, {"MY_BOOL": value}):
            assert env_bool("MY_BOOL", True) is False

    def test_returns_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert env_bool("MISSING_KEY", True) is True
            assert env_bool("MISSING_KEY", False) is False

    def test_unrecognised_value_logs_warning_and_returns_default(self, caplog):
        with patch.dict(os.environ, {"MY_BOOL": "tru"}):
            with caplog.at_level(logging.WARNING):
                result = env_bool("MY_BOOL", False)
            assert result is False
            assert "Unrecognised boolean value" in caplog.text
            assert "MY_BOOL" in caplog.text

    def test_unrecognised_value_returns_true_default(self, caplog):
        with patch.dict(os.environ, {"MY_BOOL": "yep"}):
            with caplog.at_level(logging.WARNING):
                result = env_bool("MY_BOOL", True)
            assert result is True

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"MY_BOOL": "  true  "}):
            assert env_bool("MY_BOOL", False) is True


# ------------------------------------------------------------------
# TelemetryServiceSettings.from_env
# ------------------------------------------------------------------


class TestTelemetryServiceSettingsFromEnv:
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            s = TelemetryServiceSettings.from_env()
        assert s.config_api_url == "http://config-api:8100"
        assert s.poll_interval_seconds == 5
        assert s.mqtt_publish_qos == 1
        assert s.log_level == "INFO"
        assert s.is_remote is False

    def test_custom_values(self):
        env = {
            "CONFIG_API_URL": "http://custom:9000",
            "ROBOT_ID": "bot-7",
            "IS_REMOTE": "true",
            "REMOTE_ADDRESS": "10.0.0.1",
            "POLL_INTERVAL_SECONDS": "10",
            "LOG_LEVEL": "DEBUG",
        }
        with patch.dict(os.environ, env, clear=True):
            s = TelemetryServiceSettings.from_env()
        assert s.config_api_url == "http://custom:9000"
        assert s.robot_id == "bot-7"
        assert s.is_remote is True
        assert s.remote_address == "10.0.0.1"
        assert s.poll_interval_seconds == 10
        assert s.log_level == "DEBUG"


# ------------------------------------------------------------------
# effective_janus_ws_* properties
# ------------------------------------------------------------------


class TestEffectiveJanusUrls:
    def test_override_depth(self):
        s = TelemetryServiceSettings(janus_ws_depth="ws://custom:1234")
        assert s.effective_janus_ws_depth == "ws://custom:1234"

    def test_override_color(self):
        s = TelemetryServiceSettings(janus_ws_color="wss://custom:5678")
        assert s.effective_janus_ws_color == "wss://custom:5678"

    def test_local_defaults(self):
        s = TelemetryServiceSettings(local_ip="192.168.1.1", is_remote=False)
        assert s.effective_janus_ws_depth == "ws://192.168.1.1:8188"
        assert s.effective_janus_ws_color == "ws://192.168.1.1:8189"

    def test_remote_uses_wss(self):
        s = TelemetryServiceSettings(is_remote=True, remote_address="cloud.example.com")
        assert s.effective_janus_ws_depth == "wss://cloud.example.com:8188"
        assert s.effective_janus_ws_color == "wss://cloud.example.com:8189"

    def test_empty_host_returns_empty(self):
        s = TelemetryServiceSettings(local_ip="", is_remote=False)
        assert s.effective_janus_ws_depth == ""
        assert s.effective_janus_ws_color == ""


# ------------------------------------------------------------------
# get_settings singleton
# ------------------------------------------------------------------


class TestGetSettings:
    def test_returns_same_instance(self):
        with patch.dict(os.environ, {}, clear=True):
            s1 = get_settings()
            s2 = get_settings()
        assert s1 is s2


# ------------------------------------------------------------------
# fetch_mqtt_connection_config
# ------------------------------------------------------------------


class TestFetchMqttConnectionConfig:
    def test_builds_mqtt_connection_config(self):
        fake_data = {
            "broker": "mqtt.example.com",
            "broker_port": 1883,
            "mqtt_user": "user",
            "mqtt_password": "pass",
            "mqtt_use_tls": False,
        }
        settings = TelemetryServiceSettings(robot_id="bot-1")

        with patch("config._fetch_mqtt") as mock_fetch:
            mock_fetch.side_effect = lambda url, build_fn, **kw: build_fn(fake_data)
            result = fetch_mqtt_connection_config(settings)

        assert result.broker == "mqtt.example.com"
        assert result.broker_port == 1883
        assert result.robot_id == "bot-1"
        assert result.client_id == "bot-1-telemetry"
        assert result.mqtt_use_tls is False

    def test_tls_auto_switches_port(self):
        fake_data = {
            "broker": "mqtt.example.com",
            "broker_port": 1883,  # default non-TLS port
            "mqtt_user": "user",
            "mqtt_password": "pass",
            "mqtt_use_tls": True,
        }
        settings = TelemetryServiceSettings(robot_id="bot-1")

        with patch("config._fetch_mqtt") as mock_fetch:
            mock_fetch.side_effect = lambda url, build_fn, **kw: build_fn(fake_data)
            result = fetch_mqtt_connection_config(settings)

        assert result.broker_port == 8883  # switched to TLS port

    def test_custom_client_id(self):
        fake_data = {
            "broker": "mqtt.example.com",
            "mqtt_user": "",
            "mqtt_password": "",
        }
        settings = TelemetryServiceSettings(robot_id="bot-1", mqtt_client_id="custom-id")

        with patch("config._fetch_mqtt") as mock_fetch:
            mock_fetch.side_effect = lambda url, build_fn, **kw: build_fn(fake_data)
            result = fetch_mqtt_connection_config(settings)

        assert result.client_id == "custom-id"

    def test_qos_clamped(self):
        """QoS is clamped to 0–2 in _build, but __post_init__ rejects out-of-range."""
        fake_data = {"broker": "b", "mqtt_user": "", "mqtt_password": ""}
        settings = TelemetryServiceSettings(mqtt_publish_qos=2)

        with patch("config._fetch_mqtt") as mock_fetch:
            mock_fetch.side_effect = lambda url, build_fn, **kw: build_fn(fake_data)
            result = fetch_mqtt_connection_config(settings)

        assert result.mqtt_publish_qos == 2


# ------------------------------------------------------------------
# TelemetryServiceSettings __post_init__ validation
# ------------------------------------------------------------------


class TestSettingsValidation:
    def test_valid_defaults(self):
        s = TelemetryServiceSettings()
        assert s.poll_interval_seconds == 5

    def test_poll_interval_zero_raises(self):
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            TelemetryServiceSettings(poll_interval_seconds=0)

    def test_poll_interval_negative_raises(self):
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            TelemetryServiceSettings(poll_interval_seconds=-1)

    def test_http_timeout_zero_raises(self):
        with pytest.raises(ValueError, match="telemetry_http_timeout"):
            TelemetryServiceSettings(telemetry_http_timeout=0)

    def test_websocket_check_timeout_zero_raises(self):
        with pytest.raises(ValueError, match="websocket_check_timeout"):
            TelemetryServiceSettings(websocket_check_timeout=0)

    def test_websocket_check_interval_zero_raises(self):
        with pytest.raises(ValueError, match="websocket_check_interval"):
            TelemetryServiceSettings(websocket_check_interval=0)

    def test_qos_out_of_range_raises(self):
        with pytest.raises(ValueError, match="mqtt_publish_qos"):
            TelemetryServiceSettings(mqtt_publish_qos=5)

    def test_qos_negative_raises(self):
        with pytest.raises(ValueError, match="mqtt_publish_qos"):
            TelemetryServiceSettings(mqtt_publish_qos=-1)

    def test_multiple_errors_reported(self):
        with pytest.raises(ValueError) as exc_info:
            TelemetryServiceSettings(poll_interval_seconds=0, telemetry_http_timeout=-1)
        msg = str(exc_info.value)
        assert "poll_interval_seconds" in msg
        assert "telemetry_http_timeout" in msg


# ------------------------------------------------------------------
# validate_robot_id
# ------------------------------------------------------------------


class TestValidateRobotId:
    @pytest.mark.parametrize(
        "robot_id",
        [
            "robot-01",
            "my_robot.v2",
            "Robot123",
            "a",
        ],
    )
    def test_valid_ids(self, robot_id):
        assert validate_robot_id(robot_id) == robot_id

    @pytest.mark.parametrize(
        "robot_id",
        [
            "",
            "robot/01",
            "robot+01",
            "robot#01",
            "robot 01",
            "robot\t01",
        ],
    )
    def test_invalid_ids_raise(self, robot_id):
        with pytest.raises(ValueError, match="Invalid robot_id"):
            validate_robot_id(robot_id)

    def test_from_env_validates(self):
        env = {"ROBOT_ID": "invalid/id"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="Invalid robot_id"):
                TelemetryServiceSettings.from_env()
