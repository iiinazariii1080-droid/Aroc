"""Structured JSON logging configuration.

Provides a JSON formatter for machine-parseable log output.  When
``CAM_LOG_FORMAT=json`` is set, all log records are emitted as single-line
JSON objects suitable for ingestion by Loki, Fluentd, or similar.

Default (``CAM_LOG_FORMAT=text``) preserves the traditional human-readable
format for local development.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ts, level, logger, message, module, funcName, lineno, host, service, exc.
    ``host`` identifies which node (color vs depth) emitted the record — critical
    for dual-node deployments where logs are aggregated in Loki/Fluentd.
    If ``request_id`` is attached to the record (via RequestTracingMiddleware),
    it is included for cross-node correlation.
    """

    def __init__(self) -> None:
        super().__init__()
        import socket
        self._hostname = socket.gethostname()
        self._service = os.environ.get("CAM_TYPE", "color_camera")

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "host": self._hostname,
            "service": self._service,
        }
        # Propagate request_id if available (set by RequestTracingMiddleware)
        request_id = getattr(record, "request_id", None)
        if request_id:
            entry["request_id"] = request_id

        if record.exc_info and record.exc_info[1]:
            entry["exc"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging() -> None:
    """Configure root logging based on ``CAM_LOG_FORMAT`` env var.

    Values:
      - ``json``  — structured JSON (one object per line)
      - ``text``  — traditional human-readable (default)
    """
    log_format = os.environ.get("CAM_LOG_FORMAT", "text").lower()
    log_level = os.environ.get("CAM_LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove any existing handlers to avoid duplicate output.
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(handler)
