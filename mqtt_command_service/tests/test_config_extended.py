"""Tests for config.py — config loading, values, mqtt auth, etc."""

import os
from unittest.mock import MagicMock, patch

import pytest

import config
from config import (
    BridgeConfig,
    _get_config_value,
    _get_config_value_bool,
    get_broker_config,
    is_mqtt_auth_configured,
    load_service_configs,
    save_config_variable,
)


class TestIsMqttAuthConfigured:
    def test_none(self):
        assert is_mqtt_auth_configured(None) is False

    def test_empty(self):
        assert is_mqtt_auth_configured("") is False

    def test_whitespace(self):
        assert is_mqtt_auth_configured("  ") is False

    def test_anonymous(self):
        assert is_mqtt_auth_configured("anonymous") is False
        assert is_mqtt_auth_configured("Anonymous") is False
        assert is_mqtt_auth_configured("ANONYMOUS") is False

    def test_valid_user(self):
        assert is_mqtt_auth_configured("admin") is True
        assert is_mqtt_auth_configured("user123") is True


class TestBridgeConfig:
    def test_topic_patterns(self):
        cfg = BridgeConfig(
            broker="localhost",
            broker_port=1883,
            mqtt_user="u",
            mqtt_password="p",
            robot_id="r-1",
            client_id="c-1",
            http_timeout=5.0,
            task_poll_interval=1.0,
            task_poll_timeout=30.0,
        )
        assert cfg.cmd_topic_pattern == "aroc/robot/r-1/cmd/+"
        assert cfg.resp_base_topic == "aroc/robot/r-1/resp"
        assert cfg.command_topic_pattern == "aroc/robot/r-1/commands/+"
        assert cfg.status_base_topic == "aroc/robot/r-1/status"
        assert cfg.config_topic_pattern == "aroc/robot/r-1/config/+"


class TestGetConfigValue:
    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value="db-value")
    def test_db_priority(self, mock_db):
        assert _get_config_value("KEY", "default") == "db-value"

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value=None)
    def test_env_fallback(self, mock_db):
        with patch.dict(os.environ, {"KEY": "env-value"}):
            assert _get_config_value("KEY", "default") == "env-value"

    @patch("config.DB_AVAILABLE", False)
    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            # Ensure KEY is not in env
            os.environ.pop("KEY", None)
            assert _get_config_value("KEY", "default") == "default"

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value="db-val")
    def test_use_db_false(self, mock_db):
        """With use_db=False, skips DB even if available."""
        os.environ.pop("KEY", None)
        assert _get_config_value("KEY", "default", use_db=False) == "default"
        mock_db.assert_not_called()


class TestGetConfigValueBool:
    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value="true")
    def test_db_true(self, _):
        assert _get_config_value_bool("KEY", False) is True

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value="false")
    def test_db_false(self, _):
        assert _get_config_value_bool("KEY", True) is False

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value=None)
    def test_env_true(self, _):
        with patch.dict(os.environ, {"KEY": "1"}):
            assert _get_config_value_bool("KEY", False) is True

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value=None)
    def test_env_yes(self, _):
        with patch.dict(os.environ, {"KEY": "yes"}):
            assert _get_config_value_bool("KEY", False) is True

    @patch("config.DB_AVAILABLE", False)
    def test_default(self):
        os.environ.pop("KEY", None)
        assert _get_config_value_bool("KEY", True) is True
        assert _get_config_value_bool("KEY", False) is False


class TestSaveConfigVariable:
    @patch("config.DB_AVAILABLE", True)
    @patch("config.save_config_to_db", return_value=True)
    def test_save_to_db(self, mock_save):
        assert save_config_variable("MQTT_BROKER", "new.broker.com") is True
        mock_save.assert_called_once()

    @patch("config.DB_AVAILABLE", True)
    @patch("config.save_config_to_db", return_value=False)
    def test_save_failure(self, mock_save):
        assert save_config_variable("MQTT_BROKER", "val") is False

    @patch("config.DB_AVAILABLE", False)
    def test_non_db_key(self):
        assert save_config_variable("UNKNOWN_KEY", "val") is False

    @patch("config.DB_AVAILABLE", True)
    @patch("config.save_config_to_db", return_value=True)
    def test_save_with_reason(self, mock_save):
        save_config_variable("MQTT_PORT", "8883", updated_by="api", reason="user change")
        _, kwargs = mock_save.call_args
        assert kwargs["updated_by"] == "api"
        assert kwargs["reason"] == "user change"


class TestGetBrokerConfig:
    @patch("app.services.config_service.get_config_service")
    def test_returns_dict(self, mock_svc):
        mock_cfg = MagicMock()
        mock_cfg.broker = "broker.local"
        mock_cfg.broker_port = 8883
        mock_cfg.mqtt_user = "user"
        mock_cfg.mqtt_password = "pass"
        mock_cfg.mqtt_use_tls = True
        mock_cfg.mqtt_tls_insecure = False
        mock_cfg.mqtt_ca_certs = "/certs/ca.crt"
        mock_cfg.mqtt_certfile = None
        mock_cfg.mqtt_keyfile = None
        mock_svc.return_value.get_config.return_value = mock_cfg

        result = get_broker_config()
        assert result["MQTT_BROKER"] == "broker.local"
        assert result["MQTT_PORT"] == 8883
        assert result["mqtt_use_tls"] is True


class TestLoadServiceConfigs:
    @patch("env_settings.get_env_settings")
    def test_local_services(self, mock_env):
        mock_env.return_value.api_port = 7900
        mock_env.return_value.service_use_local = True
        mock_env.return_value.service_map_json = None
        mock_env.return_value.robot_service_url = "http://robot:8110"
        mock_env.return_value.igus_service_url = "http://igus:8101"
        mock_env.return_value.xarm_service_url = "http://xarm:8102"
        mock_env.return_value.symovo_service_url = "http://symovo:7905"

        services = load_service_configs("192.168.1.10", "192.168.1.11")
        assert "robot" in services
        assert services["robot"].watch_tasks is True
        assert "depth_camera" in services  # only in local mode
        assert services["robot"].base_url == "http://192.168.1.10:8110"

    @patch("env_settings.get_env_settings")
    def test_json_override(self, mock_env):
        mock_env.return_value.api_port = 7900
        mock_env.return_value.service_use_local = False
        mock_env.return_value.service_map_json = '{"custom":{"base_url":"http://custom:9000","watch_tasks":true}}'
        mock_env.return_value.robot_service_url = "http://robot:8110"
        mock_env.return_value.igus_service_url = "http://igus:8101"
        mock_env.return_value.xarm_service_url = "http://xarm:8102"
        mock_env.return_value.symovo_service_url = "http://symovo:7905"

        services = load_service_configs("10.0.0.1", "10.0.0.2")
        assert "custom" in services
        assert services["custom"].watch_tasks is True

    @patch("env_settings.get_env_settings")
    def test_invalid_json_override(self, mock_env):
        mock_env.return_value.api_port = 7900
        mock_env.return_value.service_use_local = False
        mock_env.return_value.service_map_json = "not-json{{"
        mock_env.return_value.robot_service_url = "http://robot:8110"
        mock_env.return_value.igus_service_url = "http://igus:8101"
        mock_env.return_value.xarm_service_url = "http://xarm:8102"
        mock_env.return_value.symovo_service_url = "http://symovo:7905"

        # Should not raise, just warn
        services = load_service_configs("10.0.0.1", "10.0.0.2")
        assert "robot" in services


class TestMigrateEnvToDb:
    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value=None)
    @patch("config.save_config_to_db", return_value=True)
    def test_migrates_env_values(self, mock_save, mock_get):
        config._migration_done = False
        with patch.dict(os.environ, {"MQTT_BROKER": "test.mqtt.com", "MQTT_PORT": "1883"}):
            config.migrate_env_to_db()
        assert config._migration_done is True
        assert mock_save.call_count >= 1

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value="existing")
    @patch("config.save_config_to_db")
    def test_skips_existing(self, mock_save, mock_get):
        config._migration_done = False
        with patch.dict(os.environ, {"MQTT_BROKER": "test.mqtt.com"}):
            config.migrate_env_to_db()
        mock_save.assert_not_called()

    @patch("config.DB_AVAILABLE", False)
    def test_no_db(self):
        config._migration_done = False
        config.migrate_env_to_db()
        assert config._migration_done is False

    @patch("config.DB_AVAILABLE", True)
    @patch("config.get_config_from_db", return_value=None)
    @patch("config.save_config_to_db", return_value=True)
    def test_skips_placeholder(self, mock_save, mock_get):
        config._migration_done = False
        with patch.dict(os.environ, {"MQTT_BROKER": "/path/to/something"}, clear=False):
            config.migrate_env_to_db()
        # Verify that /path/to placeholder was NOT saved
        for call_args in mock_save.call_args_list:
            key = call_args[0][0]
            val = call_args[0][1]
            if key == "MQTT_BROKER":
                pytest.fail(f"Placeholder MQTT_BROKER should not be saved, but was: {val}")
