"""Logging configuration with optional JSON output.

Set ``LOG_FORMAT=json`` for structured logging (ELK/Loki compatible).
Default is ``text`` (human-readable, same as before).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "command_id": getattr(record, "command_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, default=str)


class _SafeTextFormatter(logging.Formatter):
    """Text formatter that injects a default command_id if missing."""

    _FMT = "%(asctime)s %(levelname)s [%(name)s] [cid=%(command_id)s] [rid=%(request_id)s] %(message)s"

    def __init__(self):
        super().__init__(self._FMT)

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "command_id"):
            record.command_id = "-"  # type: ignore[attr-defined]
        if not hasattr(record, "request_id"):
            record.request_id = "-"  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Set up root logger with the chosen format.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        fmt: ``"text"`` for human-readable output, ``"json"`` for structured JSON lines.
    """
    from services.log_utils import install_command_id_filter, install_request_id_filter

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stderr)
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(_SafeTextFormatter())

    root.handlers.clear()
    root.addHandler(handler)

    install_command_id_filter()
    install_request_id_filter()
