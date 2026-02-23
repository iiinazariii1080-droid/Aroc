"""
Unit tests for payload validation utilities.

These tests use property-based testing principles to find edge cases and bugs.
"""

from app.utils.payload_validation import (
    has_any_config_parameter,
    validate_dict_payload,
    validate_headers,
    validate_required_field,
)


class TestValidateDictPayload:
    """Tests for validate_dict_payload function."""

    def test_valid_dict(self):
        """Normal case: valid dictionary."""
        payload = {"key": "value", "number": 42}
        result, error = validate_dict_payload(payload)
        assert result == payload
        assert error is None

    def test_empty_dict(self):
        """Edge case: empty dictionary."""
        payload = {}
        result, error = validate_dict_payload(payload)
        assert result == {}
        assert error is None

    def test_none(self):
        """Error case: None."""
        result, error = validate_dict_payload(None)
        assert result is None
        assert error is not None
        assert "must be a JSON object" in error

    def test_string(self):
        """Error case: string instead of dict."""
        result, error = validate_dict_payload("not a dict")
        assert result is None
        assert error is not None
        assert "must be a JSON object" in error

    def test_list(self):
        """Error case: list instead of dict."""
        result, error = validate_dict_payload([1, 2, 3])
        assert result is None
        assert error is not None

    def test_number(self):
        """Error case: number instead of dict."""
        result, error = validate_dict_payload(42)
        assert result is None
        assert error is not None

    def test_bool(self):
        """Error case: boolean instead of dict."""
        result, error = validate_dict_payload(True)
        assert result is None
        assert error is not None

    def test_custom_error_context(self):
        """Test custom error context in message."""
        result, error = validate_dict_payload("invalid", error_context="Command payload")
        assert result is None
        assert "Command payload" in error

    def test_nested_dict(self):
        """Edge case: nested dictionaries."""
        payload = {"outer": {"inner": {"deep": "value"}}}
        result, error = validate_dict_payload(payload)
        assert result == payload
        assert error is None

    def test_dict_with_none_values(self):
        """Edge case: dict with None values."""
        payload = {"key1": None, "key2": "value"}
        result, error = validate_dict_payload(payload)
        assert result == payload
        assert error is None


class TestValidateRequiredField:
    """Tests for validate_required_field function."""

    def test_field_exists(self):
        """Normal case: field exists."""
        data = {"id": "123", "name": "test"}
        value, error = validate_required_field(data, "id")
        assert value == "123"
        assert error is None

    def test_field_missing(self):
        """Error case: field is missing."""
        data = {"name": "test"}
        value, error = validate_required_field(data, "id")
        assert value is None
        assert error is not None
        assert "id is required" in error

    def test_field_is_none(self):
        """Error case: field exists but is None."""
        data = {"id": None, "name": "test"}
        value, error = validate_required_field(data, "id")
        assert value is None
        assert error is not None
        assert "id is required" in error

    def test_field_is_empty_string(self):
        """Edge case: field is empty string (should pass, empty string is not None)."""
        data = {"id": "", "name": "test"}
        value, error = validate_required_field(data, "id")
        assert value == ""
        assert error is None

    def test_field_is_zero(self):
        """Edge case: field is 0 (should pass, 0 is not None)."""
        data = {"id": 0, "name": "test"}
        value, error = validate_required_field(data, "id")
        assert value == 0
        assert error is None

    def test_field_is_false(self):
        """Edge case: field is False (should pass, False is not None)."""
        data = {"id": False, "name": "test"}
        value, error = validate_required_field(data, "id")
        assert value is False
        assert error is None

    def test_custom_error_context(self):
        """Test custom error context."""
        data = {}
        value, error = validate_required_field(data, "id", error_context="Command")
        assert value is None
        assert "Command" in error or "id is required" in error


class TestValidateHeaders:
    """Tests for validate_headers function."""

    def test_valid_headers(self):
        """Normal case: valid headers dict."""
        headers = {"Content-Type": "application/json", "Authorization": "Bearer token"}
        result, error = validate_headers(headers)
        assert result == headers
        assert error is None

    def test_none_headers(self):
        """Edge case: None headers (should return empty dict)."""
        result, error = validate_headers(None)
        assert result == {}
        assert error is None

    def test_empty_dict(self):
        """Edge case: empty headers dict."""
        headers = {}
        result, error = validate_headers(headers)
        assert result == {}
        assert error is None

    def test_string_instead_of_dict(self):
        """Error case: string instead of dict."""
        result, error = validate_headers("invalid")
        assert result is None
        assert error is not None
        assert "headers must be an object" in error

    def test_list_instead_of_dict(self):
        """Error case: list instead of dict."""
        result, error = validate_headers(["header1", "header2"])
        assert result is None
        assert error is not None

    def test_headers_with_none_values(self):
        """Edge case: headers with None values (should be filtered out)."""
        headers = {"Content-Type": "application/json", "Authorization": None, "X-Custom": "value"}
        result, error = validate_headers(headers)
        assert error is None
        assert "Content-Type" in result
        assert "X-Custom" in result
        assert "Authorization" not in result  # None values are filtered

    def test_headers_value_conversion_to_string(self):
        """Edge case: non-string values should be converted to strings."""
        headers = {"Content-Length": 123, "X-Flag": True, "X-Number": 42.5}
        result, error = validate_headers(headers)
        assert error is None
        assert result["Content-Length"] == "123"
        assert result["X-Flag"] == "True"
        assert result["X-Number"] == "42.5"

    def test_headers_key_conversion_to_string(self):
        """Edge case: non-string keys should be converted to strings."""
        headers = {123: "value", True: "value2"}
        result, error = validate_headers(headers)
        assert error is None
        assert "123" in result
        assert "True" in result


class TestHasAnyConfigParameter:
    """Tests for has_any_config_parameter function."""

    def test_at_least_one_present(self):
        """Normal case: at least one parameter is present."""
        data = {"broker": "localhost", "port": None, "user": None}
        param_names = ["broker", "port", "user"]
        result = has_any_config_parameter(data, param_names)
        assert result is True

    def test_all_none(self):
        """Edge case: all parameters are None."""
        data = {"broker": None, "port": None, "user": None}
        param_names = ["broker", "port", "user"]
        result = has_any_config_parameter(data, param_names)
        assert result is False

    def test_all_missing(self):
        """Edge case: all parameters are missing."""
        data = {"other": "value"}
        param_names = ["broker", "port", "user"]
        result = has_any_config_parameter(data, param_names)
        assert result is False

    def test_empty_data(self):
        """Edge case: empty data dict."""
        data = {}
        param_names = ["broker", "port"]
        result = has_any_config_parameter(data, param_names)
        assert result is False

    def test_empty_param_names(self):
        """Edge case: empty parameter names list."""
        data = {"broker": "localhost"}
        param_names = []
        result = has_any_config_parameter(data, param_names)
        assert result is False

    def test_multiple_present(self):
        """Normal case: multiple parameters present."""
        data = {"broker": "localhost", "port": 1883, "user": "admin"}
        param_names = ["broker", "port", "user"]
        result = has_any_config_parameter(data, param_names)
        assert result is True

    def test_zero_value(self):
        """Edge case: parameter is 0 (should be considered present)."""
        data = {"broker": "localhost", "port": 0}
        param_names = ["broker", "port"]
        result = has_any_config_parameter(data, param_names)
        assert result is True

    def test_false_value(self):
        """Edge case: parameter is False (should be considered present)."""
        data = {"broker": "localhost", "use_tls": False}
        param_names = ["broker", "use_tls"]
        result = has_any_config_parameter(data, param_names)
        assert result is True

    def test_empty_string_value(self):
        """Edge case: parameter is empty string (should be considered present)."""
        data = {"broker": "localhost", "password": ""}
        param_names = ["broker", "password"]
        result = has_any_config_parameter(data, param_names)
        assert result is True









