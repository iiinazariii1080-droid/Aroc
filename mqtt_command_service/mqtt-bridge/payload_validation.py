"""Payload validation utilities."""

from typing import Any


def validate_dict_payload(payload: Any, error_context: str = "payload") -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, f"{error_context} must be a JSON object"
    return payload, None


def validate_required_field(
    data: dict[str, Any], field_name: str, error_context: str = "request"
) -> tuple[Any | None, str | None]:
    value = data.get(field_name)
    if value is None:
        return None, f"{field_name} is required"
    return value, None


_MAX_HEADER_KEY_LENGTH = 256
_MAX_HEADER_VALUE_LENGTH = 8192
_MAX_HEADER_COUNT = 64


def validate_headers(headers: Any, error_context: str = "request") -> tuple[dict[str, str] | None, str | None]:
    if headers is None:
        return {}, None
    if not isinstance(headers, dict):
        return None, f"{error_context}: headers must be an object"
    if len(headers) > _MAX_HEADER_COUNT:
        return None, f"{error_context}: too many headers (max {_MAX_HEADER_COUNT})"
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if value is not None:
            str_key = str(key)
            str_value = str(value)
            if len(str_key) > _MAX_HEADER_KEY_LENGTH:
                return None, f"{error_context}: header key too long (max {_MAX_HEADER_KEY_LENGTH} chars)"
            if len(str_value) > _MAX_HEADER_VALUE_LENGTH:
                return None, f"{error_context}: header value too long (max {_MAX_HEADER_VALUE_LENGTH} chars)"
            if "\r" in str_key or "\n" in str_key or "\r" in str_value or "\n" in str_value:
                return None, f"{error_context}: header contains illegal CR/LF characters"
            normalized[str_key] = str_value
    return normalized, None
