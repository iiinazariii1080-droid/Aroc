from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

_PROCESS_START_TIME = time.time()
_LOGGER = logging.getLogger(__name__)


class MetricsRegistry:
    _LATENCY_BUCKET_BOUNDS_MS = (1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0)

    # Maximum distinct metric keys per dict.  Prevents unbounded memory growth
    # if dynamic paths are accidentally added to the API.
    _MAX_CARDINALITY = 500

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests_total: dict[tuple[str, str, int], int] = defaultdict(int)
        self._errors_total: dict[tuple[str, str, int, str], int] = defaultdict(int)
        self._drive_operation_errors_total: dict[tuple[str, str, int], int] = defaultdict(int)
        self._legacy_api_requests_total: dict[tuple[str, str], int] = defaultdict(int)
        self._latency_sum_ms: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self._latency_max_ms: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_buckets: dict[tuple[str, str, str], int] = defaultdict(int)
        self._cardinality_overflows_total: int = 0
        self._cardinality_overflow_warned: bool = False

    def _guard_cardinality(self, d: dict, key: object) -> bool:
        """Return True if key is safe to record; False if cardinality cap hit."""
        if key in d:
            return True
        if len(d) < self._MAX_CARDINALITY:
            return True
        self._cardinality_overflows_total += 1
        if not self._cardinality_overflow_warned:
            self._cardinality_overflow_warned = True
            _LOGGER.warning(
                "Metrics cardinality cap reached (%d keys) — new metric keys "
                "will be silently dropped. This usually means dynamic path "
                "segments are leaking into metric labels.",
                self._MAX_CARDINALITY,
            )
        return False

    def observe_http(self, method: str, path: str, status_code: int, latency_ms: float) -> None:
        key_status = (method.upper(), path, int(status_code))
        key_latency = (method.upper(), path)
        with self._lock:
            if not self._guard_cardinality(self._requests_total, key_status):
                return  # silently drop to prevent memory leak
            self._requests_total[key_status] += 1
            self._latency_sum_ms[key_latency] += latency_ms
            self._latency_count[key_latency] += 1
            if latency_ms > self._latency_max_ms[key_latency]:
                self._latency_max_ms[key_latency] = latency_ms
            # Cumulative histogram buckets: a sample with latency_ms=7 must
            # increment le=10, le=25, ..., le=+Inf — all bounds >= latency_ms.
            for bound in self._LATENCY_BUCKET_BOUNDS_MS:
                if latency_ms <= bound:
                    self._latency_buckets[(key_latency[0], key_latency[1], f"{bound:g}")] += 1
            # +Inf always
            self._latency_buckets[(key_latency[0], key_latency[1], "+Inf")] += 1

    def observe_error(self, method: str, path: str, status_code: int, code: str) -> None:
        key = (method.upper(), path, int(status_code), code)
        with self._lock:
            if not self._guard_cardinality(self._errors_total, key):
                return
            self._errors_total[key] += 1

    def observe_drive_operation_error(self, operation: str, code: str, status_code: int) -> None:
        key = (operation, code, int(status_code))
        with self._lock:
            if not self._guard_cardinality(self._drive_operation_errors_total, key):
                return
            self._drive_operation_errors_total[key] += 1

    def observe_legacy_api_request(self, path: str, phase: str) -> None:
        key = (path, phase)
        with self._lock:
            if not self._guard_cardinality(self._legacy_api_requests_total, key):
                return
            self._legacy_api_requests_total[key] += 1

    def render_prometheus(self) -> str:
        with self._lock:
            return self._render_prometheus_locked()

    def _render_prometheus_locked(self) -> str:
        """Render Prometheus text format. Must be called with self._lock held."""
        lines: list[str] = []

        lines.append("# HELP igus_http_requests_total Total HTTP requests by method/path/status")
        lines.append("# TYPE igus_http_requests_total counter")
        for (method, path, status_code), value in sorted(self._requests_total.items()):
            lines.append(
                f'igus_http_requests_total{{method="{method}",path="{path}",status="{status_code}"}} {value}'
            )

        lines.append("# HELP igus_http_errors_total Total HTTP errors by method/path/status/error_code")
        lines.append("# TYPE igus_http_errors_total counter")
        for (method, path, status_code, code), value in sorted(self._errors_total.items()):
            lines.append(
                f'igus_http_errors_total{{method="{method}",path="{path}",status="{status_code}",code="{code}"}} {value}'
            )

        lines.append("# HELP igus_drive_operation_errors_total Total drive operation errors by operation/error_code/status")
        lines.append("# TYPE igus_drive_operation_errors_total counter")
        for (operation, code, status_code), value in sorted(self._drive_operation_errors_total.items()):
            lines.append(
                f'igus_drive_operation_errors_total{{operation="{operation}",code="{code}",status="{status_code}"}} {value}'
            )

        lines.append("# HELP igus_legacy_api_requests_total Total requests to legacy API endpoints by path/phase")
        lines.append("# TYPE igus_legacy_api_requests_total counter")
        for (path, phase), value in sorted(self._legacy_api_requests_total.items()):
            lines.append(
                f'igus_legacy_api_requests_total{{path="{path}",phase="{phase}"}} {value}'
            )

        # Histogram: single TYPE declaration covers _bucket, _sum, _count.
        # Output order: _bucket (all le values), _sum, _count.
        lines.append("# HELP igus_http_request_latency_ms Histogram of HTTP request latencies in milliseconds")
        lines.append("# TYPE igus_http_request_latency_ms histogram")
        for (method, path, le), value in sorted(
            self._latency_buckets.items(),
            key=lambda item: (
                item[0][0],
                item[0][1],
                float("inf") if item[0][2] == "+Inf" else float(item[0][2]),
            ),
        ):
            lines.append(
                f'igus_http_request_latency_ms_bucket{{method="{method}",path="{path}",le="{le}"}} {value}'
            )
        for (method, path), value in sorted(self._latency_sum_ms.items()):  # type: ignore[assignment]
            lines.append(f'igus_http_request_latency_ms_sum{{method="{method}",path="{path}"}} {value:.3f}')
        for (method, path), value in sorted(self._latency_count.items()):
            lines.append(f'igus_http_request_latency_ms_count{{method="{method}",path="{path}"}} {value}')

        lines.append("# HELP igus_http_request_latency_ms_max Maximum observed HTTP request latency in milliseconds")
        lines.append("# TYPE igus_http_request_latency_ms_max gauge")
        for (method, path), value in sorted(self._latency_max_ms.items()):  # type: ignore[assignment]
            lines.append(f'igus_http_request_latency_ms_max{{method="{method}",path="{path}"}} {value:.3f}')

        # Cardinality overflow counter
        lines.append("# HELP igus_metrics_cardinality_overflows_total Number of metric samples dropped due to cardinality cap")
        lines.append("# TYPE igus_metrics_cardinality_overflows_total counter")
        lines.append(f"igus_metrics_cardinality_overflows_total {self._cardinality_overflows_total}")

        # Process metrics
        lines.append("# HELP process_start_time_seconds Start time of the process since unix epoch in seconds")
        lines.append("# TYPE process_start_time_seconds gauge")
        lines.append(f"process_start_time_seconds {_PROCESS_START_TIME:.3f}")

        return "\n".join(lines) + "\n"
