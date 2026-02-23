"""
Tests for refactored MQTT client service functions.

These tests focus on finding bugs in the refactored code:
- TLS certificate validation
- Config change detection
- Logging methods
"""
from unittest.mock import Mock, patch

import pytest

from app.services.config_service import ConfigChangeEvent, ConfigService
from app.services.mqtt_client_service import (
    UnifiedMQTTClient,
)
from app.services.mqtt_tls import normalize_cert_paths, validate_tls_certificates
from config import BridgeConfig


@pytest.fixture
def mock_config_service():
    """Create mock ConfigService."""
    service = Mock(spec=ConfigService)

    mock_config = BridgeConfig(
        broker="test.broker.com",
        broker_port=1883,
        mqtt_user="test_user",
        mqtt_password="test_pass",
        robot_id="test_robot",
        client_id="test_client",
        http_timeout=5.0,
        task_poll_interval=1.0,
        task_poll_timeout=120.0,
        services={},
        mqtt_use_tls=False,
        mqtt_ca_certs=None,
        mqtt_certfile=None,
        mqtt_keyfile=None,
        mqtt_tls_insecure=False,
    )

    service.get_config.return_value = mock_config
    service.subscribe = Mock()
    service.unsubscribe = Mock()

    return service


@pytest.fixture
def client_instance(mock_config_service):
    """Create UnifiedMQTTClient instance."""
    with patch('app.services.mqtt_client_service.mqtt_client') as mock_mqtt_module:
        mock_client_class = Mock()
        mock_client_instance = Mock()
        mock_client_class.return_value = mock_client_instance

        mock_client_instance.loop_start = Mock()
        mock_client_instance.loop_stop = Mock()
        mock_client_instance.disconnect = Mock()
        mock_client_instance.connect = Mock()
        mock_client_instance.subscribe = Mock(return_value=(0, 1))
        mock_client_instance.unsubscribe = Mock(return_value=(0,))
        mock_client_instance.publish = Mock(return_value=Mock(rc=0))

        mock_mqtt_module.Client = mock_client_class

        client = UnifiedMQTTClient(
            config_service=mock_config_service,
            component_name="test_component",
        )

        client._mock_client = mock_client_instance
        yield client


class TestIsBrokerConfigChange:
    """Tests for _is_broker_config_change predicate."""

    def test_broker_changed(self, client_instance):
        """Test: BROKER_CHANGED event requires reconnection."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.BROKER_CHANGED)
        assert result is True

    def test_port_changed(self, client_instance):
        """Test: PORT_CHANGED event requires reconnection."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.PORT_CHANGED)
        assert result is True

    def test_tls_changed(self, client_instance):
        """Test: TLS_CHANGED event requires reconnection."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.TLS_CHANGED)
        assert result is True

    def test_certificates_changed(self, client_instance):
        """Test: CERTIFICATES_CHANGED event requires reconnection."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.CERTIFICATES_CHANGED)
        assert result is True

    def test_full_reload(self, client_instance):
        """Test: FULL_RELOAD event requires reconnection."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.FULL_RELOAD)
        assert result is True

    def test_credentials_changed(self, client_instance):
        """Test: CREDENTIALS_CHANGED event does NOT require reconnection (bug?)."""
        result = client_instance._is_broker_config_change(ConfigChangeEvent.CREDENTIALS_CHANGED)
        # This might be a bug - credentials change might need reconnection
        # But current implementation says it doesn't
        assert result is False


class TestNormalizeCertPaths:
    """Tests for _normalize_cert_paths function."""

    def test_all_none(self):
        """Edge case: all paths are None."""
        ca, cert, key = normalize_cert_paths(None, None, None)
        assert ca is None
        assert cert is None
        assert key is None

    def test_valid_paths(self):
        """Normal case: valid paths."""
        ca, cert, key = normalize_cert_paths(
            "/path/to/ca.crt",
            "/path/to/cert.crt",
            "/path/to/key.key"
        )
        assert ca == "/path/to/ca.crt"
        assert cert == "/path/to/cert.crt"
        assert key == "/path/to/key.key"

    def test_empty_strings(self):
        """Edge case: empty strings should become None."""
        ca, cert, key = normalize_cert_paths("", "", "")
        assert ca is None
        assert cert is None
        assert key is None

    def test_whitespace_only(self):
        """Edge case: whitespace-only strings should become None."""
        ca, cert, key = normalize_cert_paths("   ", "\t", "\n")
        assert ca is None
        assert cert is None
        assert key is None

    def test_whitespace_trimmed(self):
        """Edge case: whitespace should be trimmed."""
        ca, cert, key = normalize_cert_paths(
            "  /path/to/ca.crt  ",
            "  /path/to/cert.crt  ",
            "  /path/to/key.key  "
        )
        assert ca == "/path/to/ca.crt"
        assert cert == "/path/to/cert.crt"
        assert key == "/path/to/key.key"

    def test_mixed_none_and_valid(self):
        """Edge case: mix of None and valid paths."""
        ca, cert, key = normalize_cert_paths(
            "/path/to/ca.crt",
            None,
            ""
        )
        assert ca == "/path/to/ca.crt"
        assert cert is None
        assert key is None


class TestValidateTLSCertificates:
    """Tests for _validate_tls_certificates function."""

    def test_all_files_exist(self, tmp_path):
        """Normal case: all certificate files exist."""
        ca_file = tmp_path / "ca.crt"
        cert_file = tmp_path / "cert.crt"
        key_file = tmp_path / "key.key"

        ca_file.write_text("ca content")
        cert_file.write_text("cert content")
        key_file.write_text("key content")

        errors = validate_tls_certificates(
            str(ca_file),
            str(cert_file),
            str(key_file)
        )
        assert errors == []

    def test_ca_missing(self, tmp_path):
        """Error case: CA certificate file missing."""
        cert_file = tmp_path / "cert.crt"
        key_file = tmp_path / "key.key"

        cert_file.write_text("cert content")
        key_file.write_text("key content")

        errors = validate_tls_certificates(
            "/nonexistent/ca.crt",
            str(cert_file),
            str(key_file)
        )
        assert len(errors) > 0
        assert any("CA cert" in err for err in errors)

    def test_cert_missing(self, tmp_path):
        """Error case: client certificate file missing."""
        ca_file = tmp_path / "ca.crt"
        key_file = tmp_path / "key.key"

        ca_file.write_text("ca content")
        key_file.write_text("key content")

        errors = validate_tls_certificates(
            str(ca_file),
            "/nonexistent/cert.crt",
            str(key_file)
        )
        assert len(errors) > 0
        assert any("Client cert" in err for err in errors)

    def test_key_missing(self, tmp_path):
        """Error case: client key file missing."""
        ca_file = tmp_path / "ca.crt"
        cert_file = tmp_path / "cert.crt"

        ca_file.write_text("ca content")
        cert_file.write_text("cert content")

        errors = validate_tls_certificates(
            str(ca_file),
            str(cert_file),
            "/nonexistent/key.key"
        )
        assert len(errors) > 0
        assert any("Client key" in err for err in errors)

    def test_cert_without_key(self, tmp_path):
        """Error case: cert specified but key missing."""
        ca_file = tmp_path / "ca.crt"
        cert_file = tmp_path / "cert.crt"

        ca_file.write_text("ca content")
        cert_file.write_text("cert content")

        errors = validate_tls_certificates(
            str(ca_file),
            str(cert_file),
            None
        )
        assert len(errors) > 0
        assert any("both be specified" in err for err in errors)

    def test_key_without_cert(self, tmp_path):
        """Error case: key specified but cert missing."""
        ca_file = tmp_path / "ca.crt"
        key_file = tmp_path / "key.key"

        ca_file.write_text("ca content")
        key_file.write_text("key content")

        errors = validate_tls_certificates(
            str(ca_file),
            None,
            str(key_file)
        )
        assert len(errors) > 0
        assert any("both be specified" in err for err in errors)

    def test_all_none(self):
        """Edge case: all certificates are None (system certs)."""
        errors = validate_tls_certificates(None, None, None)
        assert errors == []

    def test_ca_none_system_certs(self, tmp_path):
        """Edge case: CA is None (system certs), client cert/key provided."""
        cert_file = tmp_path / "cert.crt"
        key_file = tmp_path / "key.key"

        cert_file.write_text("cert content")
        key_file.write_text("key content")

        errors = validate_tls_certificates(
            None,
            str(cert_file),
            str(key_file)
        )
        assert errors == []

    def test_multiple_errors(self):
        """Error case: multiple files missing."""
        errors = validate_tls_certificates(
            "/nonexistent/ca.crt",
            "/nonexistent/cert.crt",
            None  # Also missing key
        )
        assert len(errors) >= 2  # At least CA missing and cert/key mismatch


class TestLoggingMethods:
    """Tests for refactored logging methods (no boolean flags)."""

    def test_log_info(self, client_instance):
        """Test: _log_info without throttling."""
        with patch('app.services.mqtt_logging.logger') as mock_logger:
            client_instance._log_info("Test message", "test_key")
            mock_logger.info.assert_called_once_with("Test message")

    def test_log_info_throttled(self, client_instance):
        """Test: _log_info_throttled with throttling."""
        with patch('app.services.mqtt_logging.logger') as mock_logger:
            # First call should log
            client_instance._log_info_throttled("Test message", "test_key")
            mock_logger.info.assert_called()

            # Second call immediately should be throttled
            mock_logger.reset_mock()
            client_instance._log_info_throttled("Test message", "test_key")
            # Should not log again (throttled)
            mock_logger.info.assert_not_called()

    def test_log_warning(self, client_instance):
        """Test: _log_warning without throttling."""
        with patch('app.services.mqtt_logging.logger') as mock_logger:
            client_instance._log_warning("Test warning", "test_key")
            mock_logger.warning.assert_called_once_with("Test warning")

    def test_log_error(self, client_instance):
        """Test: _log_error without throttling."""
        with patch('app.services.mqtt_logging.logger') as mock_logger:
            client_instance._log_error("Test error", "test_key", exc_info=True)
            mock_logger.error.assert_called_once_with("Test error", exc_info=True)

    def test_log_error_throttled(self, client_instance):
        """Test: _log_error_throttled with throttling."""
        with patch('app.services.mqtt_logging.logger') as mock_logger:
            # First call should log
            client_instance._log_error_throttled("Test error", "test_key")
            mock_logger.error.assert_called()

            # Second call immediately should be throttled
            mock_logger.reset_mock()
            client_instance._log_error_throttled("Test error", "test_key")
            mock_logger.error.assert_not_called()









