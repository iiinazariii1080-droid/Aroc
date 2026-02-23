from app import error_codes
from app.http_errors import normalize_error_detail


def test_normalize_error_detail_uses_registry_defaults_5xx() -> None:
    payload = normalize_error_detail(500, "boom")
    assert payload["code"] == error_codes.INTERNAL_ERROR.code


def test_normalize_error_detail_uses_registry_defaults_4xx() -> None:
    payload = normalize_error_detail(400, "bad")
    assert payload["code"] == error_codes.REQUEST_ERROR.code


def test_error_codes_registry_contains_core_contract_codes() -> None:
    assert error_codes.DRIVE_NOT_INITIALIZED.code == "DRIVE_NOT_INITIALIZED"
    assert error_codes.DRIVE_OFFLINE.code == "DRIVE_OFFLINE"
    assert error_codes.STATUS_READ_FAILED.code == "STATUS_READ_FAILED"
    assert error_codes.TELEMETRY_READ_FAILED.code == "TELEMETRY_READ_FAILED"
