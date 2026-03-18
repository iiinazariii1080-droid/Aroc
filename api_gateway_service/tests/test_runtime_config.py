"""Tests for validate_runtime_config() — strict mode enforcement."""

from unittest.mock import patch

import pytest

from app.core.config import validate_runtime_config, GatewaySettings


@pytest.fixture
def strict_env(monkeypatch, tmp_path):
    """Set up environment for strict runtime validation."""
    key_file = tmp_path / "robot.key"
    key_file.write_text("test-api-key", encoding="utf-8")
    monkeypatch.setenv("STRICT_RUNTIME", "true")
    monkeypatch.setenv("VERIFY_TLS", "true")
    monkeypatch.setenv("ALLOW_INSECURE_TLS", "false")
    monkeypatch.setenv("READINESS_CHECK_SERVICES", "true")
    monkeypatch.setenv("READINESS_CHECK_AUTH", "true")
    monkeypatch.setenv("GATEWAY_ADMIN_KEY", "secret-admin-key")
    monkeypatch.setenv("ROBOT_API_KEY_FILE", str(key_file))
    return key_file


def test_strict_mode_passes_with_valid_config(strict_env):
    """All checks pass when all required settings are correct."""
    s = GatewaySettings()
    with patch("app.core.config.settings", s), \
         patch("app.core.config.STRICT_RUNTIME", True):
        validate_runtime_config()  # should not raise


def test_strict_mode_fails_without_admin_key(strict_env, monkeypatch):
    """Missing GATEWAY_ADMIN_KEY is caught in strict mode."""
    monkeypatch.delenv("GATEWAY_ADMIN_KEY")
    s = GatewaySettings()
    with patch("app.core.config.settings", s), \
         patch("app.core.config.STRICT_RUNTIME", True):
        with pytest.raises(RuntimeError, match="GATEWAY_ADMIN_KEY"):
            validate_runtime_config()


def test_strict_mode_fails_with_insecure_tls(strict_env, monkeypatch):
    """ALLOW_INSECURE_TLS=true is rejected in strict mode."""
    monkeypatch.setenv("ALLOW_INSECURE_TLS", "true")
    s = GatewaySettings()
    with patch("app.core.config.settings", s), \
         patch("app.core.config.STRICT_RUNTIME", True):
        with pytest.raises(RuntimeError, match="ALLOW_INSECURE_TLS"):
            validate_runtime_config()


def test_strict_mode_fails_without_key_file(strict_env, monkeypatch):
    """Missing ROBOT_API_KEY_FILE is caught in strict mode."""
    monkeypatch.delenv("ROBOT_API_KEY_FILE")
    s = GatewaySettings()
    with patch("app.core.config.settings", s), \
         patch("app.core.config.STRICT_RUNTIME", True):
        with pytest.raises(RuntimeError, match="ROBOT_API_KEY_FILE"):
            validate_runtime_config()


def test_non_strict_mode_skips_validation(monkeypatch):
    """Non-strict mode skips all checks."""
    monkeypatch.delenv("STRICT_RUNTIME", raising=False)
    with patch("app.core.config.STRICT_RUNTIME", False):
        validate_runtime_config()  # should not raise
