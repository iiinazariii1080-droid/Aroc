"""Tests for payload validation utilities."""

from payload_validation import validate_dict_payload, validate_headers, validate_required_field


class TestValidateDictPayload:
    def test_valid_dict(self):
        data, error = validate_dict_payload({"key": "value"})
        assert data == {"key": "value"}
        assert error is None

    def test_empty_dict(self):
        data, error = validate_dict_payload({})
        assert data == {}
        assert error is None

    def test_list_rejected(self):
        data, error = validate_dict_payload([1, 2, 3])
        assert data is None
        assert error is not None

    def test_string_rejected(self):
        data, error = validate_dict_payload("not a dict")
        assert data is None
        assert error is not None

    def test_none_rejected(self):
        data, error = validate_dict_payload(None)
        assert data is None
        assert error is not None

    def test_int_rejected(self):
        data, error = validate_dict_payload(42)
        assert data is None
        assert error is not None

    def test_error_context_in_message(self):
        _, error = validate_dict_payload("bad", error_context="Test payload")
        assert "Test payload" in error


class TestValidateRequiredField:
    def test_present_field(self):
        value, error = validate_required_field({"name": "alice"}, "name")
        assert value == "alice"
        assert error is None

    def test_missing_field(self):
        value, error = validate_required_field({}, "name")
        assert value is None
        assert error is not None
        assert "name" in error

    def test_none_value_treated_as_missing(self):
        value, error = validate_required_field({"name": None}, "name")
        assert value is None
        assert error is not None

    def test_falsy_but_present_value(self):
        value, error = validate_required_field({"count": 0}, "count")
        # 0 is falsy but not None, so it should be returned
        assert value == 0
        assert error is None

    def test_empty_string_is_present(self):
        value, error = validate_required_field({"name": ""}, "name")
        assert value == ""
        assert error is None


class TestValidateHeaders:
    def test_none_returns_empty_dict(self):
        headers, error = validate_headers(None)
        assert headers == {}
        assert error is None

    def test_valid_dict(self):
        headers, error = validate_headers({"Content-Type": "application/json"})
        assert headers == {"Content-Type": "application/json"}
        assert error is None

    def test_non_dict_rejected(self):
        headers, error = validate_headers("not a dict")
        assert headers is None
        assert error is not None

    def test_list_rejected(self):
        headers, error = validate_headers(["header1", "header2"])
        assert headers is None
        assert error is not None

    def test_values_converted_to_strings(self):
        headers, error = validate_headers({"X-Count": 42})
        assert headers == {"X-Count": "42"}
        assert error is None

    def test_none_values_stripped(self):
        headers, error = validate_headers({"Keep": "value", "Drop": None})
        assert headers == {"Keep": "value"}
        assert error is None

    def test_keys_converted_to_strings(self):
        headers, error = validate_headers({123: "value"})
        assert headers is not None
        assert "123" in headers
        assert error is None
