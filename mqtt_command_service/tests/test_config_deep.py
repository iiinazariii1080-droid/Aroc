"""Tests for config.py — load helpers, service configs, migration, save/get.

Targets ~40 missed statements.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from config import (
    BridgeConfig,
    _get_config_value,
    _get_config_value_bool,
    get_broker_config,
    is_mqtt_auth_configured,
    load_service_configs,
    save_config_variable,
)

# ---- is_mqtt_auth_configured ----

class TestIsMqttAuthConfigured:
    def test_none(self):
        assert is_mqtt_auth_configured(None) is False

    def test_empty_string(self):
        assert is_mqtt_auth_configured("") is False

    def test_anonymous(self):
        assert is_mqtt_auth_configured("anonymous") is False

    def test_anonymous_case(self):
        assert is_mqtt_auth_configured("Anonymous") is False

    def test_valid_user(self):
        assert is_mqtt_auth_configured("admin") is True


# ---- _get_config_value ----

class TestGetConfigValue:
    def test_from_env(self):
        with patch.dict(os.environ, {"TEST_CFG_KEY": "env_val"}, clear=False):
            assert _get_config_value("TEST_CFG_KEY", "default", use_db=False) == "env_val"

    def test_default_fallback(self):
        result = _get_config_value("NON_EXISTENT_KEY_12345", "fallback", use_db=False)
        assert result == "fallback"

    def test_db_lookup(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value="db_val"):
            assert _get_config_value("MQTT_BROKER", "default") == "db_val"

    def test_db_none_fallback_to_env(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value=None), \
             patch.dict(os.environ, {"MQTT_BROKER": "env_val"}, clear=False):
            assert _get_config_value("MQTT_BROKER", "default") == "env_val"


# ---- _get_config_value_bool ----

class TestGetConfigValueBool:
    def test_true_values(self):
        for val in ("true", "1", "yes", "on"):
            with patch.dict(os.environ, {"BOOL_KEY": val}, clear=False):
                assert _get_config_value_bool("BOOL_KEY", False, use_db=False) is True

    def test_false_values(self):
        with patch.dict(os.environ, {"BOOL_KEY": "false"}, clear=False):
            assert _get_config_value_bool("BOOL_KEY", True, use_db=False) is False

    def test_default(self):
        result = _get_config_value_bool("NONEXISTENT_BOOL_KEY_999", True, use_db=False)
        assert result is True

    def test_db_true(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value="true"):
            assert _get_config_value_bool("MQTT_USE_TLS", False) is True

    def test_db_false(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value="no"):
            assert _get_config_value_bool("MQTT_USE_TLS", True) is False


# ---- load_service_configs ----

class TestLoadServiceConfigs:
    def test_remote_services(self):
        with patch("env_settings.get_env_settings") as mock_env:
            env = mock_env.return_value
            env.api_port = 7900
            env.robot_service_url = "http://robot:8110"
            env.igus_service_url = "http://igus:8101"
            env.xarm_service_url = "http://xarm:8102"
            env.symovo_service_url = "http://symovo:7905"
            env.service_use_local = False
            env.service_map_json = ""

            services = load_service_configs("192.168.1.1", "192.168.1.2")
            assert "robot" in services
            assert services["robot"].base_url == "http://robot:8110"
            assert services["robot"].watch_tasks is True

    def test_local_services(self):
        with patch("env_settings.get_env_settings") as mock_env:
            env = mock_env.return_value
            env.api_port = 7900
            env.robot_service_url = "http://robot:8110"
            env.igus_service_url = "http://igus:8101"
            env.xarm_service_url = "http://xarm:8102"
            env.symovo_service_url = "http://symovo:7905"
            env.service_use_local = True
            env.service_map_json = ""

            services = load_service_configs("10.0.0.1", "10.0.0.2")
            assert services["robot"].base_url == "http://10.0.0.1:8110"
            assert "color_camera" in services
            assert "depth_camera" in services
            assert services["depth_camera"].base_url == "http://10.0.0.2:8900"

    def test_service_map_json_override(self):
        override = json.dumps({"custom_svc": {"base_url": "http://custom:9000", "watch_tasks": True}})
        with patch("env_settings.get_env_settings") as mock_env:
            env = mock_env.return_value
            env.api_port = 7900
            env.robot_service_url = "http://robot:8110"
            env.igus_service_url = "http://igus:8101"
            env.xarm_service_url = "http://xarm:8102"
            env.symovo_service_url = "http://symovo:7905"
            env.service_use_local = False
            env.service_map_json = override

            services = load_service_configs("10.0.0.1", "10.0.0.2")
            assert "custom_svc" in services
            assert services["custom_svc"].base_url == "http://custom:9000"
            assert services["custom_svc"].watch_tasks is True

    def test_service_map_json_string_shorthand(self):
        override = json.dumps({"simple_svc": "http://simple:8000"})
        with patch("env_settings.get_env_settings") as mock_env:
            env = mock_env.return_value
            env.api_port = 7900
            env.robot_service_url = "http://robot:8110"
            env.igus_service_url = "http://igus:8101"
            env.xarm_service_url = "http://xarm:8102"
            env.symovo_service_url = "http://symovo:7905"
            env.service_use_local = False
            env.service_map_json = override

            services = load_service_configs("10.0.0.1", "10.0.0.2")
            assert services["simple_svc"].base_url == "http://simple:8000"

    def test_service_map_json_invalid(self):
        with patch("env_settings.get_env_settings") as mock_env:
            env = mock_env.return_value
            env.api_port = 7900
            env.robot_service_url = "http://robot:8110"
            env.igus_service_url = "http://igus:8101"
            env.xarm_service_url = "http://xarm:8102"
            env.symovo_service_url = "http://symovo:7905"
            env.service_use_local = False
            env.service_map_json = "NOT_JSON"

            # Should not raise, just log warning
            services = load_service_configs("10.0.0.1", "10.0.0.2")
            assert "robot" in services


# ---- save_config_variable ----

class TestSaveConfigVariable:
    def test_save_db_key_success(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.save_config_to_db", return_value=True):
            assert save_config_variable("MQTT_BROKER", "new-host") is True

    def test_save_db_key_failure(self):
        with patch("config.DB_AVAILABLE", True), \
             patch("config.save_config_to_db", return_value=False):
            assert save_config_variable("MQTT_BROKER", "new-host") is False

    def test_save_non_db_key(self):
        result = save_config_variable("SOME_OTHER_KEY", "value")
        assert result is False


# ---- get_broker_config ----

class TestGetBrokerConfig:
    def test_returns_config_dict(self):
        mock_cfg = BridgeConfig(
            broker="test-broker",
            broker_port=1883,
            mqtt_user="user",
            mqtt_password="pass",
            robot_id="r1",
            client_id="c1",
            http_timeout=5.0,
            task_poll_interval=1.0,
            task_poll_timeout=30.0,
        )
        with patch("app.services.config_service.get_config_service") as mock_cs:
            mock_cs.return_value.get_config.return_value = mock_cfg
            result = get_broker_config()
        assert result["MQTT_BROKER"] == "test-broker"
        assert result["MQTT_PORT"] == 1883


# ---- BridgeConfig properties ----

class TestBridgeConfigProperties:
    def test_topic_patterns(self):
        cfg = BridgeConfig(
            broker="b", broker_port=1883, mqtt_user="u", mqtt_password="p",
            robot_id="robot-1", client_id="c1", http_timeout=5.0,
            task_poll_interval=1.0, task_poll_timeout=30.0,
        )
        assert cfg.cmd_topic_pattern == "aroc/robot/robot-1/cmd/+"
        assert cfg.resp_base_topic == "aroc/robot/robot-1/resp"
        assert cfg.command_topic_pattern == "aroc/robot/robot-1/commands/+"
        assert cfg.status_base_topic == "aroc/robot/robot-1/status"
        assert cfg.config_topic_pattern == "aroc/robot/robot-1/config/+"


# ---- migrate_env_to_db ----

class TestMigrateEnvToDb:
    def test_migration_runs_once(self):
        import config
        orig = config._migration_done
        try:
            config._migration_done = False
            with (
                patch("config.DB_AVAILABLE", True),
                patch.dict(os.environ, {"MQTT_BROKER": "test-broker"}, clear=False),
                patch("config.get_config_from_db", return_value=None),
                patch("config.save_config_to_db", return_value=True) as mock_save,
            ):
                config.migrate_env_to_db()
                mock_save.assert_called()
                assert config._migration_done is True
                # Second call should be no-op
                mock_save.reset_mock()
                config.migrate_env_to_db()
                mock_save.assert_not_called()
        finally:
            config._migration_done = orig

    def test_migration_skips_placeholders(self):
        import config
        orig = config._migration_done
        try:
            config._migration_done = False
            with (
                patch("config.DB_AVAILABLE", True),
                patch.dict(os.environ, {"MQTT_BROKER": "/path/to/something"}, clear=False),
                patch("config.get_config_from_db", return_value=None),
                patch("config.save_config_to_db", return_value=True) as mock_save,
            ):
                config.migrate_env_to_db()
                # Should skip placeholder values
                for call in mock_save.call_args_list:
                    assert "/path/to" not in call[0][1]
        finally:
            config._migration_done = orig

    def test_migration_skips_existing_db_values(self):
        import config
        orig = config._migration_done
        try:
            config._migration_done = False
            with (
                patch("config.DB_AVAILABLE", True),
                patch.dict(os.environ, {"MQTT_BROKER": "new-broker"}, clear=False),
                patch("config.get_config_from_db", return_value="existing-broker"),
                patch("config.save_config_to_db") as mock_save,
            ):
                config.migrate_env_to_db()
                mock_save.assert_not_called()
        finally:
            config._migration_done = orig
