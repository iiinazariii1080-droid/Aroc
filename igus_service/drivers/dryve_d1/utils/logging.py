"""Logging utilities.

Design:
- Avoid global configuration surprises: `configure_logging()` is explicit.
- Provide consistent formatting across CLI, services, and tests.
"""

from __future__ import annotations

import logging

_DEFAULT_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: int = logging.INFO,
    *,
    format: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATEFMT,
    force: bool = False,
) -> None:
    """Configure root logging.

    Args:
        level: root log level.
        format: log record format.
        datefmt: timestamp format.
        force: if True, remove existing handlers and reconfigure.
    """
    root = logging.getLogger()
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(fmt=format, datefmt=datefmt))
        root.addHandler(handler)

    root.setLevel(level)


def get_logger(name: str, level: int | None = None) -> logging.Logger:
    """Return a module logger, optionally overriding its level."""
    log = logging.getLogger(name)
    if level is not None:
        log.setLevel(level)
    return log
