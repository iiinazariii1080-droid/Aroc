"""Shared structured logging configuration.

All services should use ``configure_logging()`` instead of calling
``logging.basicConfig()`` directly.  When ``LOG_FORMAT=json`` is set,
log output is emitted as single-line JSON objects suitable for
aggregation in ELK/Loki/CloudWatch.
"""

import json
import logging
import os
import traceback

from shared.utils import now_iso


class JSONFormatter(logging.Formatter):
    """Single-line JSON log formatter for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))
        # Carry structured fields from extra dict
        for key in ("service", "robot_id", "command_id", "request_id", "correlation_id"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, ensure_ascii=False)


_TEXT_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"


def configure_logging(level: str = "INFO", service_name: str = "") -> None:
    """Configure root logger with text or JSON formatting.

    Set ``LOG_FORMAT=json`` env var to enable JSON output.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_format = os.environ.get("LOG_FORMAT", "text").lower()

    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    root.addHandler(handler)
