"""Tests for app/api/v1/endpoints/broker.py — broker configuration."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _bypass(bypass_auth):
    """All tests in this module run with auth bypassed."""


@pytest.fixture()
def api():
    with TestClient(app) as c:
        yield c


def _make_bridge_config(**overrides):
    cfg = MagicMock()
    cfg.broker = overrides.get("broker", "192.168.1.100")
    cfg.broker_port = overrides.get("broker_port", 1883)
    cfg.mqtt_user = overrides.get("mqtt_user", "admin")
    cfg.mqtt_password = overrides.get("mqtt_password", "secret")
    cfg.mqtt_use_tls = overrides.get("mqtt_use_tls", False)
    cfg.mqtt_tls_insecure = overrides.get("mqtt_tls_insecure", False)
    return cfg


# ── GET /config/broker ─────────────────────────────────────────────────


class TestGetBrokerSettings:
    @patch("app.api.v1.endpoints.broker.get_config_service")
    def test_success(self, mock_get_svc, api):
        svc = MagicMock()
        svc.get_config.return_value = _make_bridge_config()
        mock_get_svc.return_value = svc

        resp = api.get("/api/v1/config/broker")
        assert resp.status_code == 200
        body = resp.json()
        assert body["broker"] == "192.168.1.100"
        assert body["broker_port"] == 1883
        assert body["mqtt_user"] == "admin"
        # Password should be masked by default
        assert body.get("mqtt_password") != "secret" or "****" in str(body)

    @patch("app.api.v1.endpoints.broker.get_config_service")
    def test_config_service_failure(self, mock_get_svc, api):
        mock_get_svc.side_effect = RuntimeError("db broken")
        resp = api.get("/api/v1/config/broker")
        assert resp.status_code >= 400


# ── POST /config/broker ────────────────────────────────────────────────


class TestUpdateBrokerSettings:
    @patch("app.api.v1.endpoints.broker.get_config_service")
    @patch("app.api.v1.endpoints.broker.test_mqtt_connection")
    @patch("app.api.v1.endpoints.broker.prepare_config_updates")
    def test_update_success(self, mock_prepare, mock_conn_test, mock_get_svc, api):
        cfg = _make_bridge_config()
        svc = MagicMock()
        svc.get_config.return_value = cfg
        svc.update_config.return_value = True
        mock_get_svc.return_value = svc
        mock_conn_test.return_value = (True, None)
        mock_prepare.return_value = {"MQTT_BROKER": "10.0.0.1"}

        resp = api.post(
            "/api/v1/config/broker",
            json={"broker": "10.0.0.1"},
        )
        assert resp.status_code == 200
        svc.update_config.assert_called_once()

    @patch("app.api.v1.endpoints.broker.get_config_service")
    @patch("app.api.v1.endpoints.broker.test_mqtt_connection")
    @patch("app.api.v1.endpoints.broker.prepare_config_updates")
    @patch("constants.CERT_CA_FILE")
    @patch("constants.CERT_CLIENT_CERT_FILE")
    @patch("constants.CERT_CLIENT_KEY_FILE")
    def test_connection_test_failure(
        self, mock_key, mock_cert, mock_ca, mock_prepare, mock_conn_test, mock_get_svc, api
    ):
        # Cert path mocks
        for m in (mock_ca, mock_cert, mock_key):
            m.exists.return_value = False

        svc = MagicMock()
        svc.get_config.return_value = _make_bridge_config()
        mock_get_svc.return_value = svc
        mock_conn_test.return_value = (False, "Connection refused")
        mock_prepare.return_value = {"MQTT_BROKER": "192.168.1.200"}

        resp = api.post(
            "/api/v1/config/broker",
            json={"broker": "192.168.1.200"},
        )
        assert resp.status_code == 400
        assert "Connection refused" in resp.json()["detail"]
        assert "Connection refused" in resp.json()["detail"]

    @patch("app.api.v1.endpoints.broker.get_config_service")
    @patch("app.api.v1.endpoints.broker.test_mqtt_connection")
    @patch("app.api.v1.endpoints.broker.prepare_config_updates")
    def test_no_changes(self, mock_prepare, mock_conn_test, mock_get_svc, api):
        svc = MagicMock()
        svc.get_config.return_value = _make_bridge_config()
        mock_get_svc.return_value = svc
        mock_conn_test.return_value = (True, None)
        mock_prepare.return_value = {}  # empty → no changes

        resp = api.post("/api/v1/config/broker", json={})
        assert resp.status_code == 200
        svc.update_config.assert_not_called()

    @patch("app.api.v1.endpoints.broker.get_config_service")
    @patch("app.api.v1.endpoints.broker.test_mqtt_connection")
    @patch("app.api.v1.endpoints.broker.prepare_config_updates")
    def test_tls_auto_port_switch(self, mock_prepare, mock_conn_test, mock_get_svc, api):
        """Enabling TLS on default port (1883) should auto-switch to 8883."""
        svc = MagicMock()
        svc.get_config.return_value = _make_bridge_config(broker_port=1883)
        svc.update_config.return_value = True
        mock_get_svc.return_value = svc
        mock_conn_test.return_value = (True, None)
        mock_prepare.return_value = {"MQTT_USE_TLS": "true"}

        resp = api.post(
            "/api/v1/config/broker",
            json={"mqtt_use_tls": True},
        )
        assert resp.status_code == 200
        # Verify the update included auto port switch
        call_args = svc.update_config.call_args[0][0]
        assert call_args.get("MQTT_PORT") == "8883"

    @patch("app.api.v1.endpoints.broker.get_config_service")
    @patch("app.api.v1.endpoints.broker.test_mqtt_connection")
    @patch("app.api.v1.endpoints.broker.prepare_config_updates")
    def test_save_failure(self, mock_prepare, mock_conn_test, mock_get_svc, api):
        svc = MagicMock()
        svc.get_config.return_value = _make_bridge_config()
        svc.update_config.return_value = False  # save fails
        mock_get_svc.return_value = svc
        mock_conn_test.return_value = (True, None)
        mock_prepare.return_value = {"MQTT_BROKER": "10.0.0.1"}

        resp = api.post(
            "/api/v1/config/broker",
            json={"broker": "10.0.0.1"},
        )
        assert resp.status_code >= 400

    def test_invalid_port(self, api):
        """Port outside 1-65535 should fail validation."""
        resp = api.post(
            "/api/v1/config/broker",
            json={"broker_port": 99999},
        )
        assert resp.status_code == 422
