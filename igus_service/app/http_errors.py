from __future__ import annotations

from typing import Any

from app import error_codes


def error_detail(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "code": code,
    }
    if details is not None:
        payload["details"] = details
    return payload


def normalize_error_detail(status_code: int, detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict) and "message" in detail and "code" in detail:
        payload = dict(detail)
        payload.setdefault("details", None)
        return payload

    message = detail if isinstance(detail, str) else "Request failed"

    default_code = error_codes.INTERNAL_ERROR.code if status_code >= 500 else error_codes.REQUEST_ERROR.code
    return error_detail(code=default_code, message=message, details=None)
