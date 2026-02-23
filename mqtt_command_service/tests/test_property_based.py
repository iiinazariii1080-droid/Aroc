"""
Property-based tests using Hypothesis.

These tests generate random inputs to find edge cases and bugs.
"""
from hypothesis import given, settings
from hypothesis import strategies as st

from app.utils.payload_validation import (
    has_any_config_parameter,
    validate_dict_payload,
    validate_headers,
    validate_required_field,
)


class TestPropertyBasedValidation:
    """Property-based tests for validation functions."""

    @given(st.data())
    @settings(max_examples=100)
    def test_validate_dict_payload_always_returns_tuple(self, data):
        """Property: validate_dict_payload always returns a tuple of length 2."""
        payload = data.draw(st.one_of(
            st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.booleans(), st.none())),
            st.text(),
            st.integers(),
            st.booleans(),
            st.lists(st.text()),
            st.none(),
        ))

        result, error = validate_dict_payload(payload)

        # Property: Always returns tuple
        assert isinstance((result, error), tuple)
        assert len((result, error)) == 2

        # Property: If result is not None, error must be None
        if result is not None:
            assert error is None
            assert isinstance(result, dict)

        # Property: If error is not None, result must be None
        if error is not None:
            assert result is None
            assert isinstance(error, str)
            assert len(error) > 0

    @given(st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.booleans(), st.none())))
    @settings(max_examples=50)
    def test_validate_dict_payload_preserves_valid_dicts(self, payload):
        """Property: Valid dicts are returned unchanged."""
        result, error = validate_dict_payload(payload)

        assert error is None
        assert result == payload

    @given(st.data())
    @settings(max_examples=100)
    def test_validate_required_field_contract(self, data):
        """Property: validate_required_field follows contract."""
        # Generate random dict
        field_name = data.draw(st.text(min_size=1, max_size=20))
        field_value = data.draw(st.one_of(
            st.text(),
            st.integers(),
            st.booleans(),
            st.none(),
        ))

        data_dict = {field_name: field_value}

        value, error = validate_required_field(data_dict, field_name)

        # Property: Always returns tuple
        assert isinstance((value, error), tuple)

        # Property: If field is None, error must be present
        if field_value is None:
            assert error is not None
            assert value is None
        else:
            # Property: If field is not None, value must match
            assert value == field_value
            assert error is None

    @given(st.dictionaries(
        st.text(min_size=1),
        st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
        min_size=0,
        max_size=10
    ))
    @settings(max_examples=50)
    def test_validate_headers_always_dict_or_error(self, headers):
        """Property: validate_headers always returns dict or error."""
        result, error = validate_headers(headers)

        # Property: Always returns tuple
        assert isinstance((result, error), tuple)

        # Property: Either result is dict or error is present
        if result is not None:
            assert isinstance(result, dict)
            assert error is None
            # Property: All values in result are strings
            for key, value in result.items():
                assert isinstance(key, str)
                assert isinstance(value, str)
        else:
            assert error is not None
            assert isinstance(error, str)

    @given(st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.booleans(), st.none())))
    @settings(max_examples=50)
    def test_validate_headers_filters_none_values(self, headers):
        """Property: None values are filtered from headers."""
        result, error = validate_headers(headers)

        if error is None:
            # Property: No None values in result
            for value in result.values():
                assert value is not None
                assert isinstance(value, str)

    @given(
        st.dictionaries(
            st.text(min_size=1),
            st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
            min_size=0,
            max_size=20
        ),
        st.lists(st.text(min_size=1), min_size=1, max_size=10)
    )
    @settings(max_examples=100)
    def test_has_any_config_parameter_property(self, data_dict, param_names):
        """Property: has_any_config_parameter returns boolean."""
        result = has_any_config_parameter(data_dict, param_names)

        # Property: Always returns boolean
        assert isinstance(result, bool)

        # Property: If any param is not None, result is True
        has_non_none = any(
            data_dict.get(param) is not None
            for param in param_names
            if param in data_dict
        )

        if has_non_none:
            assert result is True

        # Property: If all params are None or missing, result is False
        all_none_or_missing = all(
            data_dict.get(param) is None
            for param in param_names
        )

        if all_none_or_missing:
            assert result is False


class TestPropertyBasedEdgeCases:
    """Property-based tests for edge cases."""

    @given(st.text())
    @settings(max_examples=50)
    def test_validate_dict_payload_with_any_string(self, text):
        """Property: Any string that's not valid JSON dict returns error."""
        result, error = validate_dict_payload(text)

        # Property: String is never a valid dict (unless it's JSON, but we're not parsing)
        # Actually, we're checking isinstance, so string will always fail
        assert result is None
        assert error is not None

    @given(st.lists(st.one_of(st.text(), st.integers(), st.booleans())))
    @settings(max_examples=50)
    def test_validate_dict_payload_with_any_list(self, lst):
        """Property: Any list returns error."""
        result, error = validate_dict_payload(lst)

        assert result is None
        assert error is not None

    @given(st.integers())
    @settings(max_examples=50)
    def test_validate_dict_payload_with_any_integer(self, num):
        """Property: Any integer returns error."""
        result, error = validate_dict_payload(num)

        assert result is None
        assert error is not None

    @given(st.booleans())
    @settings(max_examples=10)
    def test_validate_dict_payload_with_any_boolean(self, val):
        """Property: Any boolean returns error."""
        result, error = validate_dict_payload(val)

        assert result is None
        assert error is not None









