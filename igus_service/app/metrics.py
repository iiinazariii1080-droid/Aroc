from __future__ import annotations

from collections import defaultdict
from threading import Lock


class MetricsRegistry:
    _LATENCY_BUCKET_BOUNDS_MS = (1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0)

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

    def observe_http(self, method: str, path: str, status_code: int, latency_ms: float) -> None:
        key_status = (method.upper(), path, int(status_code))
        key_latency = (method.upper(), path)
        with self._lock:
            self._requests_total[key_status] += 1
            self._latency_sum_ms[key_latency] += latency_ms
            self._latency_count[key_latency] += 1
            if latency_ms > self._latency_max_ms[key_latency]:
                self._latency_max_ms[key_latency] = latency_ms
            for bound in self._LATENCY_BUCKET_BOUNDS_MS:
                if latency_ms <= bound:
                    self._latency_buckets[(key_latency[0], key_latency[1], f"{bound:g}")] += 1
            self._latency_buckets[(key_latency[0], key_latency[1], "+Inf")] += 1

    def observe_error(self, method: str, path: str, status_code: int, code: str) -> None:
        with self._lock:
            self._errors_total[(method.upper(), path, int(status_code), code)] += 1

    def observe_drive_operation_error(self, operation: str, code: str, status_code: int) -> None:
        with self._lock:
            self._drive_operation_errors_total[(operation, code, int(status_code))] += 1

    def observe_legacy_api_request(self, path: str, phase: str) -> None:
        with self._lock:
            self._legacy_api_requests_total[(path, phase)] += 1

    def render_prometheus(self) -> str:
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

        lines.append("# HELP igus_http_request_latency_ms_sum Sum of HTTP request latencies in milliseconds")
        lines.append("# TYPE igus_http_request_latency_ms_sum counter")
        for (method, path), value in sorted(self._latency_sum_ms.items()):  # type: ignore[assignment]
            lines.append(f'igus_http_request_latency_ms_sum{{method="{method}",path="{path}"}} {value:.3f}')

        lines.append("# HELP igus_http_request_latency_ms_bucket Histogram buckets for HTTP request latencies in milliseconds")
        lines.append("# TYPE igus_http_request_latency_ms_bucket counter")
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

        lines.append("# HELP igus_http_request_latency_ms_count Count of observed HTTP request latencies")
        lines.append("# TYPE igus_http_request_latency_ms_count counter")
        for (method, path), value in sorted(self._latency_count.items()):
            lines.append(f'igus_http_request_latency_ms_count{{method="{method}",path="{path}"}} {value}')

        lines.append("# HELP igus_http_request_latency_ms_max Maximum observed HTTP request latency in milliseconds")
        lines.append("# TYPE igus_http_request_latency_ms_max gauge")
        for (method, path), value in sorted(self._latency_max_ms.items()):  # type: ignore[assignment]
            lines.append(f'igus_http_request_latency_ms_max{{method="{method}",path="{path}"}} {value:.3f}')

        return "\n".join(lines) + "\n"
