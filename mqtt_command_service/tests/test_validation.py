"""Tests for validation utilities (app/utils/validation.py)."""
import pytest

from app.utils.validation import validate_broker_address, validate_hostname


class TestValidateHostname:
    @pytest.mark.parametrize("hostname", [
        "example.com",
        "sub.domain.example.com",
        "my-host.example.com",
        "a.b",
    ])
    def test_valid_hostnames(self, hostname):
        valid, err = validate_hostname(hostname)
        assert valid, f"Expected valid: {hostname}, err={err}"

    def test_empty_hostname(self):
        valid, _err = validate_hostname("")
        assert not valid

    def test_too_long_hostname(self):
        valid, err = validate_hostname("a" * 254)
        assert not valid
        assert "too long" in err.lower()

    def test_label_too_long(self):
        valid, err = validate_hostname("a" * 64 + ".com")
        assert not valid
        assert "too long" in err.lower()

    def test_empty_label(self):
        valid, _err = validate_hostname("host..com")
        assert not valid

    def test_label_starts_with_hyphen(self):
        valid, _err = validate_hostname("-invalid.com")
        assert not valid


class TestValidateBrokerAddress:
    @pytest.mark.parametrize("addr", [
        "192.168.1.1",
        "10.0.0.1",
        "::1",
        "[2001:db8::1]",
        "broker.example.com",
        "localhost",
    ])
    def test_valid_addresses(self, addr):
        valid, err = validate_broker_address(addr)
        assert valid, f"Expected valid: {addr}, err={err}"

    @pytest.mark.parametrize("addr", [
        "",
        "  ",
        "123",       # Just digits
    ])
    def test_invalid_addresses(self, addr):
        valid, _err = validate_broker_address(addr)
        assert not valid, f"Expected invalid: {addr}"

    def test_none_like_empty(self):
        valid, _err = validate_broker_address("")
        assert not valid
