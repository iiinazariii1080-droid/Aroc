"""
Payload validation utilities.

This module provides reusable validation functions following the principle:
- Clear predicates with explicit names
- Fail-fast validation
- No hidden side effects
"""
from typing import Any


def validate_dict_payload(
    payload: Any,
    error_context: str = "payload"
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Validate that payload is a dictionary.

    Preconditions:
        - payload can be any type

    Postconditions:
        - Returns (dict, None) if payload is a dict
        - Returns (None, error_message) if payload is not a dict

    Examples:
        >>> validate_dict_payload({"key": "value"})
        ({'key': 'value'}, None)
        >>> validate_dict_payload("string")
        (None, 'payload must be a JSON object')
        >>> validate_dict_payload(None)
        (None, 'payload must be a JSON object')
    """
    if not isinstance(payload, dict):
        error_msg = f"{error_context} must be a JSON object"
        return None, error_msg
    return payload, None


def validate_required_field(
    data: dict[str, Any],
    field_name: str,
    error_context: str = "request"
) -> tuple[Any | None, str | None]:
    """
    Validate that a required field exists in data.

    Preconditions:
        - data is a dict
        - field_name is a non-empty string

    Postconditions:
        - Returns (value, None) if field exists and is not None
        - Returns (None, error_message) if field is missing or None

    Examples:
        >>> validate_required_field({"id": "123"}, "id")
        ('123', None)
        >>> validate_required_field({"id": None}, "id")
        (None, 'id is required')
        >>> validate_required_field({}, "id")
        (None, 'id is required')
    """
    value = data.get(field_name)
    if value is None:
        error_msg = f"{field_name} is required"
        return None, error_msg
    return value, None


def validate_headers(
    headers: Any,
    error_context: str = "request"
) -> tuple[dict[str, str] | None, str | None]:
    """
    Validate that headers is a dictionary of strings.

    Preconditions:
        - headers can be any type

    Postconditions:
        - Returns (dict, None) if headers is a dict (or None/empty dict)
        - Returns (None, error_message) if headers is not a dict

    Examples:
        >>> validate_headers({"Content-Type": "application/json"})
        ({'Content-Type': 'application/json'}, None)
        >>> validate_headers(None)
        ({}, None)
        >>> validate_headers("invalid")
        (None, 'headers must be an object')
    """
    if headers is None:
        return {}, None

    if not isinstance(headers, dict):
        error_msg = f"{error_context}: headers must be an object"
        return None, error_msg

    # Convert all values to strings (headers must be strings)
    normalized_headers: dict[str, str] = {}
    for key, value in headers.items():
        if value is not None:
            normalized_headers[str(key)] = str(value)

    return normalized_headers, None


def has_any_config_parameter(
    data: dict[str, Any],
    parameter_names: list[str]
) -> bool:
    """
    Check if at least one of the specified parameters is present and not None.

    Preconditions:
        - data is a dict
        - parameter_names is a non-empty list

    Postconditions:
        - Returns True if at least one parameter exists and is not None
        - Returns False if all parameters are None or missing

    Examples:
        >>> has_any_config_parameter({"broker": "localhost"}, ["broker", "port"])
        True
        >>> has_any_config_parameter({"broker": None, "port": None}, ["broker", "port"])
        False
        >>> has_any_config_parameter({}, ["broker", "port"])
        False
    """
    return any(
        data.get(param) is not None
        for param in parameter_names
    )

