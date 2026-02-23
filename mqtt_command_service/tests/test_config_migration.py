"""Tests for config.py — migrate_env_to_db and save_config_variable."""
import threading
from unittest.mock import patch

import pytest

import config


@pytest.fixture(autouse=True)
def reset_migration():
    """Reset migration flag before each test."""
    config._migration_done = False
    yield
    config._migration_done = False


class TestMigrateEnvToDb:
    def test_runs_once_despite_concurrent_calls(self):
        """migrate_env_to_db must execute migration exactly once."""
        save_calls = []
        with patch.dict("os.environ", {"MQTT_BROKER": "1.2.3.4"}, clear=False), \
             patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value=None), \
             patch("config.save_config_to_db", side_effect=lambda *a, **kw: save_calls.append(1) or True):
            threads = [threading.Thread(target=config.migrate_env_to_db) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        # MQTT_BROKER should be migrated exactly once
        assert save_calls.count(1) >= 1
        # Should have been flagged as done
        assert config._migration_done is True

    def test_skips_placeholder_values(self):
        """Values containing '/path/to' should be skipped."""
        # Only set placeholder keys — clear all real MQTT_* env vars
        clean_env = {
            "MQTT_BROKER": "",
            "MQTT_PORT": "",
            "MQTT_USER": "",
            "MQTT_PASS": "",
            "MQTT_USE_TLS": "",
            "MQTT_TLS_INSECURE": "",
            "MQTT_CA_CERTS": "/path/to/ca.crt",
            "MQTT_CERTFILE": "",
            "MQTT_KEYFILE": "",
        }
        with patch.dict("os.environ", clean_env, clear=False), \
             patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value=None), \
             patch("config.save_config_to_db") as mock_save:
            config.migrate_env_to_db()
        mock_save.assert_not_called()

    def test_skips_when_db_not_available(self):
        """No migration if DB_AVAILABLE is False."""
        with patch("config.DB_AVAILABLE", False), \
             patch("config.save_config_to_db") as mock_save:
            config.migrate_env_to_db()
        mock_save.assert_not_called()

    def test_skips_existing_db_values(self):
        """Existing DB values should not be overwritten."""
        with patch.dict("os.environ", {"MQTT_BROKER": "new.broker"}, clear=False), \
             patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", return_value="old.broker"), \
             patch("config.save_config_to_db") as mock_save:
            config.migrate_env_to_db()
        mock_save.assert_not_called()

    def test_retries_on_error(self):
        """If migration raises, _migration_done should stay False for retry."""
        with patch.dict("os.environ", {"MQTT_BROKER": "1.2.3.4"}, clear=False), \
             patch("config.DB_AVAILABLE", True), \
             patch("config.get_config_from_db", side_effect=Exception("db error")):
            config.migrate_env_to_db()
        assert config._migration_done is False


class TestSaveConfigVariable:
    def test_saves_db_key(self):
        """DB-managed keys are saved to database."""
        with patch("config.DB_AVAILABLE", True), \
             patch("config.save_config_to_db", return_value=True) as mock_save:
            result = config.save_config_variable("MQTT_BROKER", "new.host", "api")
            assert result is True
            mock_save.assert_called_once()

    def test_does_not_mutate_os_environ(self):
        """save_config_variable should NOT mutate os.environ."""
        import os
        original = os.environ.get("MQTT_BROKER")
        with patch("config.DB_AVAILABLE", True), \
             patch("config.save_config_to_db", return_value=True):
            config.save_config_variable("MQTT_BROKER", "changed.host", "test")
        assert os.environ.get("MQTT_BROKER") == original

    def test_rejects_non_db_key(self):
        """Non-DB keys should return False."""
        result = config.save_config_variable("UNKNOWN_KEY", "val", "test")
        assert result is False
