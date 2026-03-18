from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any, Dict

from prometheus_client import Counter, Gauge, Histogram

from .utils import iso_now

# ── Prometheus instruments ───────────────────────────────────────
PROM_PROXY_REQUESTS = Counter(
    "gateway_proxy_requests_total",
    "Total proxy requests",
    ["service", "status"],
)
PROM_PROXY_DURATION = Histogram(
    "gateway_proxy_duration_seconds",
    "Proxy request duration",
    ["service"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
PROM_AUTH_ATTEMPTS = Counter(
    "gateway_auth_attempts_total",
    "Auth token request attempts",
    ["result", "reason"],
)
PROM_CB_STATE = Gauge(
    "gateway_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["service"],
)


# threading.Lock is intentional here: all operations under the lock are pure
# in-memory dict mutations (nanoseconds) with no I/O or await points, so the
# event loop is never meaningfully blocked.  asyncio.Lock would require making
# every caller async and would break the sync test harness.
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
    PROM_PROXY_REQUESTS.labels(service=service, status=str(status_code)).inc()
    with _lock:
        bucket = _service_bucket(service)
        bucket["total"] += 1
        bucket["statuses"][str(status_code)] += 1
        if status_code >= 500:
            bucket["errors"] += 1
            err_reason = reason or f"status_{status_code}"
            bucket["error_reasons"][err_reason] += 1
            bucket["last_error_reason"] = err_reason
            bucket["last_error_at"] = iso_now()


def record_proxy_duration(service: str, duration_s: float) -> None:
    """Record proxy request duration for Prometheus histogram."""
    PROM_PROXY_DURATION.labels(service=service).observe(duration_s)


def record_auth_result(success: bool, reason: str | None = None) -> None:
    result_label = "success" if success else "failure"
    PROM_AUTH_ATTEMPTS.labels(result=result_label, reason=reason or "none").inc()
    with _lock:
        _auth["attempts"] += 1
        if success:
            return
        _auth["failures"] += 1
        err_reason = reason or "auth_failure"
        _auth["failure_reasons"][err_reason] += 1
        _auth["last_failure_reason"] = err_reason
        _auth["last_failure_at"] = iso_now()


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
