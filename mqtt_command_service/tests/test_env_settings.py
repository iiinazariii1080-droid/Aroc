"""Tests for env_settings.py — centralized env var parsing."""
import os
from unittest.mock import patch

import pytest

from env_settings import EnvSettings, get_env_settings, reset_env_settings


@pytest.fixture(autouse=True)
def _reset():
    """Ensure singleton is cleared between tests."""
    reset_env_settings()
    yield
    reset_env_settings()


class TestFromEnv:
    def test_defaults(self):
        """With no env vars, defaults are used."""
        with patch.dict(os.environ, {}, clear=True):
            s = EnvSettings.from_env()
        assert s.http_timeout == 5.0
        assert s.api_port == 7900
        assert s.service_use_local is False
        assert s.log_level == "INFO"

    def test_overrides(self):
        env = {
            "HTTP_TIMEOUT": "10.5",
            "API_PORT": "9000",
            "SERVICE_USE_LOCAL": "1",
            "LOG_LEVEL": "DEBUG",
        }
        with patch.dict(os.environ, env, clear=True):
            s = EnvSettings.from_env()
        assert s.http_timeout == 10.5
        assert s.api_port == 9000
        assert s.service_use_local is True
        assert s.log_level == "DEBUG"

    def test_invalid_float_raises(self):
        with patch.dict(os.environ, {"HTTP_TIMEOUT": "not_a_number"}, clear=True):
            with pytest.raises(ValueError, match="HTTP_TIMEOUT"):
                EnvSettings.from_env()

    def test_invalid_int_raises(self):
        with patch.dict(os.environ, {"API_PORT": "abc"}, clear=True):
            with pytest.raises(ValueError, match="API_PORT"):
                EnvSettings.from_env()

    def test_depth_camera_defaults_to_local_ip(self):
        with patch.dict(os.environ, {"LOCAL_IP": "10.0.0.5"}, clear=True):
            s = EnvSettings.from_env()
        assert s.effective_depth_camera_ip == "10.0.0.5"

    def test_depth_camera_explicit(self):
        env = {"LOCAL_IP": "10.0.0.5", "DEPTH_CAMERA_IP": "10.0.0.99"}
        with patch.dict(os.environ, env, clear=True):
            s = EnvSettings.from_env()
        assert s.effective_depth_camera_ip == "10.0.0.99"

    def test_bool_parsing_variants(self):
        for truthy in ("true", "1", "yes", "on", "True", "YES"):
            with patch.dict(os.environ, {"AUTH_DISABLED": truthy}, clear=True):
                s = EnvSettings.from_env()
            assert s.auth_disabled is True, f"Failed for {truthy}"

        for falsy in ("false", "0", "no", "off", "anything"):
            with patch.dict(os.environ, {"AUTH_DISABLED": falsy}, clear=True):
                s = EnvSettings.from_env()
            assert s.auth_disabled is False, f"Failed for {falsy}"


class TestSingleton:
    def test_cached(self):
        reset_env_settings()
        a = get_env_settings()
        b = get_env_settings()
        assert a is b

    def test_reset_clears_cache(self):
        a = get_env_settings()
        reset_env_settings()
        b = get_env_settings()
        assert a is not b


class TestParseLongOperations:
    def test_valid_json(self):
        s = EnvSettings(long_operations_timeouts='{"igus": {"/move": 30.0}}')
        result = s.parse_long_operations()
        assert result == {"igus": {"/move": 30.0}}

    def test_invalid_json(self):
        s = EnvSettings(long_operations_timeouts="not json")
        assert s.parse_long_operations() is None

    def test_none(self):
        s = EnvSettings(long_operations_timeouts=None)
        assert s.parse_long_operations() is None


class TestFrozen:
    def test_immutable(self):
        s = EnvSettings.from_env()
        with pytest.raises(AttributeError):
            s.http_timeout = 999.0  # type: ignore[misc]
