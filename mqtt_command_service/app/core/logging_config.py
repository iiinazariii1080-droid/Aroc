"""Logging configuration."""
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any


class SensitiveDataFilter(logging.Filter):
    """Filter to mask sensitive data in log messages."""

    # Patterns for sensitive data
    SENSITIVE_PATTERNS = [
        (r'("password"\s*:\s*")([^"]+)(")', r'\1****\3'),
        (r'("mqtt_password"\s*:\s*")([^"]+)(")', r'\1****\3'),
        (r'("api_key"\s*:\s*")([^"]+)(")', r'\1****\3'),
        (r'("token"\s*:\s*")([^"]+)(")', r'\1****\3'),
        (r'(password=)([^\s&]+)', r'\1****'),
        (r'(api_key=)([^\s&]+)', r'\1****'),
        (r'(token=)([^\s&]+)', r'\1****'),
        (r'(X-API-Key:\s*)([^\s,]+)', r'\1****'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and mask sensitive data in log record."""
        if hasattr(record, 'msg') and record.msg:
            msg = str(record.msg)
            for pattern, replacement in self.SENSITIVE_PATTERNS:
                if callable(replacement):
                    msg = re.sub(pattern, replacement, msg)
                else:
                    msg = re.sub(pattern, replacement, msg)
            record.msg = msg

        if hasattr(record, 'args') and record.args and isinstance(record.args, tuple):
            args = list(record.args)
            for i, arg in enumerate(args):
                if isinstance(arg, str):
                    for pattern, replacement in self.SENSITIVE_PATTERNS:
                        if callable(replacement):
                            args[i] = re.sub(pattern, replacement, arg)
                        else:
                            args[i] = re.sub(pattern, replacement, arg)
            record.args = tuple(args)

        return True


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, 'request_id'):
            log_data["request_id"] = record.request_id
        if hasattr(record, 'user'):
            log_data["user"] = record.user
        if hasattr(record, 'ip'):
            log_data["ip"] = record.ip
        if hasattr(record, 'method'):
            log_data["method"] = record.method
        if hasattr(record, 'path'):
            log_data["path"] = record.path
        if hasattr(record, 'status_code'):
            log_data["status_code"] = record.status_code
        if hasattr(record, 'duration'):
            log_data["duration"] = record.duration

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    log_file: str | None = None
) -> None:
    """
    Setup application logging.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON format for logs (default: False)
        log_file: Optional log file path
    """
    # Remove existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatter
    formatter: logging.Formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s"
        )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    # Add sensitive data filter
    console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        # Add sensitive data filter
        file_handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(file_handler)

    # Set level
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

