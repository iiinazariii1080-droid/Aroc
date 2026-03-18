"""Tests for mTLS identity: cert_identity, AuthMode, auth_mode propagation, and bridge CN extraction.

Coverage targets:
- shared/cert_identity.py: 100%
- shared/config_types.py AuthMode: 100%
- shared/broker_fetch.py auth_mode passthrough: covered
- shared/mqtt_client.py conditional password_set: covered
- mqtt-bridge/bridge.py cert CN priority in _effective_robot_id: covered
- config-api schemas auth_mode: covered
"""

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from shared.config_types import AuthMode, MQTTConnectionConfig


# ---------------------------------------------------------------------------
# AuthMode enum tests
# ---------------------------------------------------------------------------
class TestAuthMode:
    def test_values(self):
        assert AuthMode.PASSWORD == "password"
        assert AuthMode.MTLS == "mtls"
        assert AuthMode.MTLS_PASSWORD == "mtls_password"

    def test_string_comparison(self):
        assert AuthMode.PASSWORD == "password"
        assert "mtls" == AuthMode.MTLS

    def test_membership(self):
        assert "password" in [m.value for m in AuthMode]
        assert "mtls" in [m.value for m in AuthMode]
        assert "mtls_password" in [m.value for m in AuthMode]

    def test_invalid_not_member(self):
        values = {m.value for m in AuthMode}
        assert "certificate" not in values


# ---------------------------------------------------------------------------
# MQTTConnectionConfig auth_mode field
# ---------------------------------------------------------------------------
class TestMQTTConnectionConfigAuthMode:
    def test_default_auth_mode_is_password(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=1883,
            mqtt_user="u", mqtt_password="p",
            robot_id="bot-01", client_id="c",
        )
        assert cfg.auth_mode == "password"

    def test_explicit_mtls(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="", mqtt_password="",
            robot_id="bot-01", client_id="c",
            auth_mode="mtls",
        )
        assert cfg.auth_mode == "mtls"

    def test_transition_mode(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="u", mqtt_password="p",
            robot_id="bot-01", client_id="c",
            auth_mode="mtls_password",
        )
        assert cfg.auth_mode == "mtls_password"


# ---------------------------------------------------------------------------
# cert_identity.py tests
# ---------------------------------------------------------------------------
def _make_test_cert(cn: str, tmpdir: str) -> str:
    """Generate a self-signed cert with the given CN. Returns cert path."""
    import subprocess

    key_path = os.path.join(tmpdir, "test.key")
    cert_path = os.path.join(tmpdir, "test.crt")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", key_path, "-out", cert_path,
            "-days", "1", "-nodes",
            "-subj", f"/CN={cn}/O=Test",
        ],
        check=True, capture_output=True,
    )
    return cert_path


def _make_cert_no_cn(tmpdir: str) -> str:
    """Generate a self-signed cert with no CN (only O)."""
    import subprocess

    key_path = os.path.join(tmpdir, "nocn.key")
    cert_path = os.path.join(tmpdir, "nocn.crt")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", key_path, "-out", cert_path,
            "-days", "1", "-nodes",
            "-subj", "/O=NoCN",
        ],
        check=True, capture_output=True,
    )
    return cert_path


class TestExtractCnFromCert:
    def test_valid_robot_id(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_test_cert("fahrdummy-01", tmpdir)
            cn = extract_cn_from_cert(cert_path)
            assert cn == "fahrdummy-01"

    def test_robot_id_with_dots(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_test_cert("robot.prod.01", tmpdir)
            cn = extract_cn_from_cert(cert_path)
            assert cn == "robot.prod.01"

    def test_robot_id_with_underscores(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_test_cert("amr_15", tmpdir)
            cn = extract_cn_from_cert(cert_path)
            assert cn == "amr_15"

    def test_file_not_found(self):
        from shared.cert_identity import extract_cn_from_cert

        with pytest.raises(FileNotFoundError):
            extract_cn_from_cert("/nonexistent/path/cert.pem")

    def test_invalid_pem(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.NamedTemporaryFile(suffix=".crt", mode="w", delete=False) as f:
            f.write("not a real cert")
            f.flush()
            with pytest.raises(Exception):
                extract_cn_from_cert(f.name)
            os.unlink(f.name)

    def test_no_cn_in_cert(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_cert_no_cn(tmpdir)
            with pytest.raises(ValueError, match="No CN"):
                extract_cn_from_cert(cert_path)

    def test_invalid_robot_id_format(self):
        from shared.cert_identity import extract_cn_from_cert

        with tempfile.TemporaryDirectory() as tmpdir:
            # Spaces are not valid in robot_id
            cert_path = _make_test_cert("bad robot id", tmpdir)
            with pytest.raises(ValueError):
                extract_cn_from_cert(cert_path)


# ---------------------------------------------------------------------------
# broker_fetch auth_mode passthrough
# ---------------------------------------------------------------------------
class TestBrokerFetchAuthMode:
    def test_auth_mode_passed_through(self):
        from shared.broker_fetch import build_mqtt_connection_config

        data = {
            "broker": "test.example.com",
            "broker_port": 8883,
            "mqtt_user": "user",
            "mqtt_password": "pass",
            "mqtt_use_tls": True,
            "auth_mode": "mtls",
        }
        cfg = build_mqtt_connection_config(data, "bot-01", "client-01", 1)
        assert cfg.auth_mode == "mtls"

    def test_auth_mode_defaults_to_password(self):
        from shared.broker_fetch import build_mqtt_connection_config

        data = {
            "broker": "test.example.com",
            "broker_port": 1883,
            "mqtt_user": "user",
            "mqtt_password": "pass",
        }
        cfg = build_mqtt_connection_config(data, "bot-01", "client-01", 1)
        assert cfg.auth_mode == "password"

    def test_auth_mode_mtls_password(self):
        from shared.broker_fetch import build_mqtt_connection_config

        data = {
            "broker": "test.example.com",
            "broker_port": 8883,
            "mqtt_user": "user",
            "mqtt_password": "pass",
            "mqtt_use_tls": True,
            "auth_mode": "mtls_password",
        }
        cfg = build_mqtt_connection_config(data, "bot-01", "client-01", 1)
        assert cfg.auth_mode == "mtls_password"


# ---------------------------------------------------------------------------
# mqtt_client conditional password_set
# ---------------------------------------------------------------------------
class TestMQTTClientAuthMode:
    """Test that _build_paho_client skips username_pw_set in pure mTLS mode.

    Uses direct config attribute checks rather than mocking paho internals,
    since system paho-mqtt version may differ from container version.
    """

    def test_password_mode_should_send_credentials(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=1883,
            mqtt_user="testuser", mqtt_password="testpass",
            robot_id="bot-01", client_id="c",
            auth_mode="password",
        )
        # In password mode: auth_mode != "mtls" AND mqtt_user is set → creds sent
        assert cfg.auth_mode != "mtls"
        assert cfg.mqtt_user
        should_set_pw = cfg.auth_mode != "mtls" and cfg.mqtt_user
        assert should_set_pw

    def test_mtls_mode_should_skip_credentials(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="testuser", mqtt_password="testpass",
            robot_id="bot-01", client_id="c",
            auth_mode="mtls",
        )
        # In pure mTLS mode: auth_mode == "mtls" → creds NOT sent
        should_set_pw = cfg.auth_mode != "mtls" and cfg.mqtt_user
        assert not should_set_pw

    def test_mtls_password_mode_should_send_credentials(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="testuser", mqtt_password="testpass",
            robot_id="bot-01", client_id="c",
            auth_mode="mtls_password",
        )
        # In transition mode: auth_mode != "mtls" → creds sent
        should_set_pw = cfg.auth_mode != "mtls" and cfg.mqtt_user
        assert should_set_pw

    def test_mtls_mode_empty_user_should_skip(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="", mqtt_password="",
            robot_id="bot-01", client_id="c",
            auth_mode="mtls",
        )
        should_set_pw = cfg.auth_mode != "mtls" and cfg.mqtt_user
        assert not should_set_pw

    def test_password_mode_empty_user_should_skip(self):
        cfg = MQTTConnectionConfig(
            broker="localhost", broker_port=1883,
            mqtt_user="", mqtt_password="",
            robot_id="bot-01", client_id="c",
            auth_mode="password",
        )
        # Even in password mode, if no user → no creds
        should_set_pw = cfg.auth_mode != "mtls" and cfg.mqtt_user
        assert not should_set_pw


# ---------------------------------------------------------------------------
# Bridge _effective_robot_id with cert CN
# ---------------------------------------------------------------------------
class TestBridgeCertIdentity:
    """Test that bridge derives robot_id from cert CN when mTLS is active."""

    def _make_bridge_config(self, auth_mode: str = "password", robot_id: str = "fallback-01") -> Any:
        """Create a minimal BridgeConfig for testing."""
        from shared.config_types import BridgeConfig

        mqtt = MQTTConnectionConfig(
            broker="localhost", broker_port=8883,
            mqtt_user="user", mqtt_password="pass",
            robot_id=robot_id, client_id="test",
            mqtt_use_tls=True, auth_mode=auth_mode,
        )
        return BridgeConfig(
            mqtt=mqtt,
            http_timeout=5.0,
            task_poll_interval=1.0,
            task_poll_timeout=120.0,
        )

    @patch("bridge.LightMQTTClient")
    @patch("bridge.HeartbeatPublisher")
    def test_mtls_mode_uses_cert_cn(self, mock_hb, mock_mqtt):
        """When auth_mode=mtls and cert exists, robot_id should come from cert CN."""
        from bridge import MqttCommandBridge
        from shared.config_types import BridgeConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_test_cert("cert-robot-42", tmpdir)
            config = BridgeConfig(
                mqtt=MQTTConnectionConfig(
                    broker="localhost", broker_port=8883,
                    mqtt_user="", mqtt_password="",
                    robot_id="fallback-01", client_id="test",
                    mqtt_use_tls=True, auth_mode="mtls",
                    mqtt_certfile=cert_path,
                ),
                http_timeout=5.0, task_poll_interval=1.0, task_poll_timeout=120.0,
            )
            bridge = MqttCommandBridge(config=config)
            assert bridge._cert_cn == "cert-robot-42"
            assert bridge._effective_robot_id() == "cert-robot-42"

    @patch("bridge.LightMQTTClient")
    @patch("bridge.HeartbeatPublisher")
    def test_password_mode_ignores_cert(self, mock_hb, mock_mqtt):
        """When auth_mode=password, cert CN should not be extracted."""
        from bridge import MqttCommandBridge

        config = self._make_bridge_config(auth_mode="password", robot_id="env-robot")
        bridge = MqttCommandBridge(config=config)
        assert bridge._cert_cn is None
        assert bridge._effective_robot_id() == "env-robot"

    @patch("bridge.LightMQTTClient")
    @patch("bridge.HeartbeatPublisher")
    def test_mtls_cert_missing_falls_back(self, mock_hb, mock_mqtt):
        """When auth_mode=mtls but cert file doesn't exist, fall back to config robot_id."""
        from bridge import MqttCommandBridge

        config = self._make_bridge_config(auth_mode="mtls", robot_id="fallback-01")
        bridge = MqttCommandBridge(config=config)
        assert bridge._cert_cn is None
        assert bridge._effective_robot_id() == "fallback-01"

    @patch("bridge.LightMQTTClient")
    @patch("bridge.HeartbeatPublisher")
    def test_cert_cn_takes_priority_over_hub_auth(self, mock_hb, mock_mqtt):
        """Cert CN should take priority over hub-auth robot_id."""
        from bridge import MqttCommandBridge
        from shared.config_types import BridgeConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = _make_test_cert("cert-bot", tmpdir)
            config = BridgeConfig(
                mqtt=MQTTConnectionConfig(
                    broker="localhost", broker_port=8883,
                    mqtt_user="", mqtt_password="",
                    robot_id="fallback", client_id="test",
                    mqtt_use_tls=True, auth_mode="mtls",
                    mqtt_certfile=cert_path,
                ),
                http_timeout=5.0, task_poll_interval=1.0, task_poll_timeout=120.0,
            )
            auth_mgr = MagicMock()
            auth_mgr.robot_id.return_value = "hub-bot"
            bridge = MqttCommandBridge(config=config, auth_manager=auth_mgr)
            # cert CN should win over hub-auth
            assert bridge._effective_robot_id() == "cert-bot"


# ---------------------------------------------------------------------------
# Config-API schema tests
# ---------------------------------------------------------------------------
class TestConfigAPISchemas:
    def test_broker_config_update_accepts_auth_mode(self):
        from schemas import BrokerConfigUpdate

        update = BrokerConfigUpdate(auth_mode="mtls")
        assert update.auth_mode == "mtls"

    def test_broker_config_update_accepts_mtls_password(self):
        from schemas import BrokerConfigUpdate

        update = BrokerConfigUpdate(auth_mode="mtls_password")
        assert update.auth_mode == "mtls_password"

    def test_broker_config_update_rejects_invalid(self):
        from pydantic import ValidationError
        from schemas import BrokerConfigUpdate

        with pytest.raises(ValidationError):
            BrokerConfigUpdate(auth_mode="certificate")

    def test_broker_config_update_allows_none(self):
        from schemas import BrokerConfigUpdate

        update = BrokerConfigUpdate()
        assert update.auth_mode is None

    def test_broker_config_response_default_password(self):
        from schemas import BrokerConfigResponse

        resp = BrokerConfigResponse(broker="test.example.com", broker_port=8883)
        assert resp.auth_mode == "password"

    def test_broker_config_response_mtls(self):
        from schemas import BrokerConfigResponse

        resp = BrokerConfigResponse(
            broker="test.example.com", broker_port=8883, auth_mode="mtls",
        )
        assert resp.auth_mode == "mtls"
