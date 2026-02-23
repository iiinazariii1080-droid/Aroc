from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_lock = Lock()
_proxy: Dict[str, Dict[str, Any]] = {}
_auth: Dict[str, Any] = {
    "attempts": 0,
    "failures": 0,
    "failure_reasons": defaultdict(int),
    "last_failure_at": None,
    "last_failure_reason": None,
}


def _service_bucket(service: str) -> Dict[str, Any]:
    bucket = _proxy.get(service)
    if bucket is None:
        bucket = {
            "total": 0,
            "errors": 0,
            "statuses": defaultdict(int),
            "error_reasons": defaultdict(int),
            "last_error_at": None,
            "last_error_reason": None,
        }
        _proxy[service] = bucket
    return bucket


def record_proxy_result(service: str, status_code: int, reason: str | None = None) -> None:
    with _lock:
        bucket = _service_bucket(service)
        bucket["total"] += 1
        bucket["statuses"][str(status_code)] += 1
        if status_code >= 500:
            bucket["errors"] += 1
            err_reason = reason or f"status_{status_code}"
            bucket["error_reasons"][err_reason] += 1
            bucket["last_error_reason"] = err_reason
            bucket["last_error_at"] = _iso_now()


def record_auth_result(success: bool, reason: str | None = None) -> None:
    with _lock:
        _auth["attempts"] += 1
        if success:
            return
        _auth["failures"] += 1
        err_reason = reason or "auth_failure"
        _auth["failure_reasons"][err_reason] += 1
        _auth["last_failure_reason"] = err_reason
        _auth["last_failure_at"] = _iso_now()


def get_service_error_metrics() -> Dict[str, Any]:
    with _lock:
        services: Dict[str, Any] = {}
        for name, bucket in _proxy.items():
            total = bucket["total"]
            errors = bucket["errors"]
            services[name] = {
                "total": total,
                "errors": errors,
                "error_rate": round((errors / total), 6) if total else 0.0,
                "statuses": dict(bucket["statuses"]),
                "error_reasons": dict(bucket["error_reasons"]),
                "last_error_at": bucket["last_error_at"],
                "last_error_reason": bucket["last_error_reason"],
            }

        auth_attempts = _auth["attempts"]
        auth_failures = _auth["failures"]
        auth = {
            "attempts": auth_attempts,
            "failures": auth_failures,
            "error_rate": round((auth_failures / auth_attempts), 6) if auth_attempts else 0.0,
            "failure_reasons": dict(_auth["failure_reasons"]),
            "last_failure_at": _auth["last_failure_at"],
            "last_failure_reason": _auth["last_failure_reason"],
        }

    return {
        "proxy_services": services,
        "auth": auth,
    }


def reset_service_error_metrics() -> None:
    with _lock:
        _proxy.clear()
        _auth["attempts"] = 0
        _auth["failures"] = 0
        _auth["failure_reasons"].clear()
        _auth["last_failure_at"] = None
        _auth["last_failure_reason"] = None
