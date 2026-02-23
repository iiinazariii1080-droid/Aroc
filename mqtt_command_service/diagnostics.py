#!/usr/bin/env python3
"""
Diagnostics helpers for quickly validating HTTP connectivity to all configured
services. The script measures response times, captures HTTP status codes, and
optionally polls task status endpoints.
"""
import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from config import BridgeConfig, ServiceConfig, load_bridge_config

logger = logging.getLogger(__name__)


@dataclass
class RequestResult:
    service: str
    url: str
    method: str
    status_code: Optional[int]
    duration_ms: float
    success: bool
    error: Optional[str] = None
    body_excerpt: Optional[str] = None


@dataclass
class DiagnosticsReport:
    config_snapshot: Dict[str, Any]
    connection_tests: List[RequestResult] = field(default_factory=list)
    status_tests: List[RequestResult] = field(default_factory=list)
    task_status_tests: List[RequestResult] = field(default_factory=list)

    def to_json(self) -> str:
        payload = {
            "config": self.config_snapshot,
            "connection_tests": [asdict(r) for r in self.connection_tests],
            "status_tests": [asdict(r) for r in self.status_tests],
            "task_status_tests": [asdict(r) for r in self.task_status_tests],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)


class DiagnosticsRunner:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        repeats: int = 3,
        timeout: Optional[float] = None,
        excerpt_len: int = 200,
    ) -> None:
        self.config = config
        self.repeats = max(1, repeats)
        self.timeout = timeout or config.http_timeout
        self.excerpt_len = excerpt_len
        self._session = requests.Session()

    def run(
        self,
        services: Iterable[ServiceConfig],
        *,
        connect_path: str,
        status_path: Optional[str],
        task_id: Optional[str],
    ) -> DiagnosticsReport:
        report = DiagnosticsReport(
            config_snapshot=self._config_snapshot(),
        )

        for service in services:
            logger.info("Testing service '%s' (%s)", service.name, service.base_url)
            report.connection_tests.extend(
                self._run_connection_tests(service, connect_path)
            )

            if status_path:
                report.status_tests.extend(
                    self._run_status_tests(service, status_path)
                )

            if task_id and service.watch_tasks:
                report.task_status_tests.append(
                    self._run_task_status_test(service, task_id)
                )
        return report

    def _config_snapshot(self) -> Dict[str, Any]:
        return {
            "MQTT_BROKER": self.config.broker,
            "MQTT_PORT": self.config.broker_port,
            "robot_id": self.config.robot_id,
            "http_timeout": self.config.http_timeout,
            "task_poll_interval": self.config.task_poll_interval,
            "task_poll_timeout": self.config.task_poll_timeout,
            "services": {
                name: {
                    "base_url": svc.base_url,
                    "watch_tasks": svc.watch_tasks,
                }
                for name, svc in self.config.services.items()
            },
        }

    def _build_url(self, service: ServiceConfig, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{service.base_url}{normalized}"

    def _run_connection_tests(
        self,
        service: ServiceConfig,
        path: str,
    ) -> List[RequestResult]:
        url = self._build_url(service, path)
        results: List[RequestResult] = []

        for attempt in range(self.repeats):
            logger.debug(
                "Connection test %s/%s -> %s",
                attempt + 1,
                self.repeats,
                url,
            )
            results.append(self._perform_request(service.name, "GET", url))
        return results

    def _run_status_tests(
        self,
        service: ServiceConfig,
        status_path: str,
    ) -> List[RequestResult]:
        url = self._build_url(service, status_path)
        logger.debug("Status test -> %s", url)
        return [self._perform_request(service.name, "GET", url)]

    def _run_task_status_test(
        self,
        service: ServiceConfig,
        task_id: str,
    ) -> RequestResult:
        url = f"{service.base_url}/tasks/status/{task_id}"
        logger.debug("Task status test -> %s", url)
        return self._perform_request(service.name, "GET", url)

    def _perform_request(
        self,
        service_name: str,
        method: str,
        url: str,
    ) -> RequestResult:
        start = time.perf_counter()
        try:
            response = self._session.request(
                method=method,
                url=url,
                timeout=self.timeout,
            )
            duration_ms = (time.perf_counter() - start) * 1000
            body_excerpt = self._safe_excerpt(response)
            success = response.ok
            logger.info(
                "[%s] %s %s -> %s (%.1f ms)",
                service_name,
                method,
                url,
                response.status_code,
                duration_ms,
            )
            return RequestResult(
                service=service_name,
                url=url,
                method=method,
                status_code=response.status_code,
                duration_ms=duration_ms,
                success=success,
                body_excerpt=body_excerpt,
            )
        except requests.RequestException as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "[%s] %s %s failed after %.1f ms: %s",
                service_name,
                method,
                url,
                duration_ms,
                exc,
            )
            return RequestResult(
                service=service_name,
                url=url,
                method=method,
                status_code=None,
                duration_ms=duration_ms,
                success=False,
                error=str(exc),
            )

    def _safe_excerpt(self, response: requests.Response) -> Optional[str]:
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                body = response.json()
                serialized = json.dumps(body)
            except ValueError:
                serialized = response.text
        else:
            serialized = response.text

        if not serialized:
            return None
        excerpt = serialized[: self.excerpt_len]
        if len(serialized) > self.excerpt_len:
            excerpt += "...(truncated)"
        return excerpt


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run connectivity diagnostics against configured services.",
    )
    parser.add_argument(
        "--services",
        nargs="+",
        help="Optional subset of services to test (default: all).",
    )
    parser.add_argument(
        "--connect-path",
        default="/",
        help="Relative path used for connection latency tests (default: /).",
    )
    parser.add_argument(
        "--status-path",
        default="/status",
        help="Relative path for status checks. Pass empty string to skip.",
    )
    parser.add_argument(
        "--task-id",
        help="Optional task_id to poll via /tasks/status/<task_id> (only for watch_tasks services).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="How many times to repeat connection tests (default: 3).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Override HTTP timeout in seconds (fallbacks to config HTTP timeout).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write JSON report.",
    )
    parser.add_argument(
        "--excerpt-len",
        type=int,
        default=200,
        help="Maximum number of characters kept per response excerpt.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)

    config = load_bridge_config()
    runner = DiagnosticsRunner(
        config,
        repeats=args.repeats,
        timeout=args.timeout,
        excerpt_len=args.excerpt_len,
    )

    service_names = args.services or list(config.services.keys())
    services: List[ServiceConfig] = []
    for name in service_names:
        svc = config.services.get(name)
        if not svc:
            logger.error("Unknown service '%s' — skipping.", name)
            continue
        services.append(svc)

    if not services:
        logger.error("No valid services selected. Exiting.")
        return 1

    status_path = args.status_path or None
    report = runner.run(
        services,
        connect_path=args.connect_path,
        status_path=status_path,
        task_id=args.task_id,
    )

    if args.output:
        args.output.write_text(report.to_json(), encoding="utf-8")
        logger.info("Diagnostics report saved to %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())

