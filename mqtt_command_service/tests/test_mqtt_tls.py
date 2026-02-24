"""Tests for app/services/mqtt_tls.py — TLS helpers."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.mqtt_tls import (
    configure_tls_on_client,
    normalize_cert_paths,
    validate_tls_certificates,
)


# ── normalize_cert_paths ──────────────────────────────────────────────


class TestNormalizeCertPaths:
    def test_strips_whitespace(self):
        ca, cert, key = normalize_cert_paths("  /ca.crt ", " /cert.crt  ", "  /key.pem ")
        assert ca == "/ca.crt"
        assert cert == "/cert.crt"
        assert key == "/key.pem"

    def test_empty_to_none(self):
        ca, cert, key = normalize_cert_paths("", "  ", None)
        assert ca is None
        assert cert is None
        assert key is None

    def test_all_none(self):
        ca, cert, key = normalize_cert_paths(None, None, None)
        assert ca is None and cert is None and key is None


# ── validate_tls_certificates ────────────────────────────────────────


class TestValidateTlsCertificates:
    @patch.object(Path, "exists", return_value=True)
    def test_all_exist(self, _):
        errors = validate_tls_certificates("/ca", "/cert", "/key")
        assert errors == []

    @patch.object(Path, "exists", return_value=False)
    def test_missing_files(self, _):
        errors = validate_tls_certificates("/ca", "/cert", "/key")
        assert len(errors) >= 3

    def test_cert_without_key(self):
        with patch.object(Path, "exists", return_value=True):
            errors = validate_tls_certificates("/ca", "/cert", None)
        assert any("must both be specified" in e for e in errors)

    def test_key_without_cert(self):
        with patch.object(Path, "exists", return_value=True):
            errors = validate_tls_certificates(None, None, "/key")
        assert any("must both be specified" in e for e in errors)

    def test_all_none_is_valid(self):
        errors = validate_tls_certificates(None, None, None)
        assert errors == []


# ── configure_tls_on_client ──────────────────────────────────────────


class TestConfigureTlsOnClient:
    def test_insecure(self):
        client = MagicMock()
        configure_tls_on_client(client, "/ca", "/cert", "/key", tls_insecure=True)
        client.tls_set.assert_called_once()
        client.tls_insecure_set.assert_called_once_with(True)

    def test_secure(self):
        client = MagicMock()
        configure_tls_on_client(client, "/ca", "/cert", "/key", tls_insecure=False)
        client.tls_set.assert_called_once()
        client.tls_insecure_set.assert_not_called()

    def test_none_paths(self):
        client = MagicMock()
        configure_tls_on_client(client, None, None, None, tls_insecure=False)
        client.tls_set.assert_called_once_with(ca_certs=None, certfile=None, keyfile=None)
