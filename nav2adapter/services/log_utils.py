"""
Logging utilities for structured log fields.

Provides a LoggingAdapter that binds a command_id to every log record
emitted through it, enabling log correlation across the lifecycle of a
single navigation command.

Usage::

    from services.log_utils import CommandLogger

    _log = CommandLogger(_LOGGER, command_id)
    _log.info("Navigation started to %s", target_id)
    # Produces: ... [cid=cmd-abc123] Navigation started to dock
"""
import logging
from typing import Optional


class CommandLogger(logging.LoggerAdapter):
    """Logger adapter that injects ``command_id`` into every log record.

    Pass this in place of a plain logger when a command_id is known:

        log = CommandLogger(logger, command_id)
        log.info("Transport created: %s", transport_id)

    The ``command_id`` value appears as ``%(command_id)s`` in the formatter.
    """

    def __init__(self, logger: logging.Logger, command_id: Optional[str]):
        super().__init__(logger, extra={"command_id": command_id or "-"})

    def process(self, msg: str, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["command_id"] = self.extra["command_id"]
        return msg, kwargs


class _CommandIdFilter(logging.Filter):
    """Inject a default ``command_id`` field into records that lack one.

    Install once on the root logger so that the ``%(command_id)s`` format
    token is always valid, even for records not emitted through CommandLogger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "command_id"):
            record.command_id = "-"  # type: ignore[attr-defined]
        return True


def install_command_id_filter() -> None:
    """Add the default command_id filter to the root logger.

    Call once during application startup (after basicConfig) so that
    ``%(command_id)s`` in the format string never raises a KeyError.
    """
    root = logging.getLogger()
    for f in root.filters:
        if isinstance(f, _CommandIdFilter):
            return  # already installed
    root.addFilter(_CommandIdFilter())


class _RequestIdFilter(logging.Filter):
    """Inject ``request_id`` from contextvars into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            try:
                from app.request_context import get_request_id
                record.request_id = get_request_id()  # type: ignore[attr-defined]
            except Exception:
                record.request_id = "-"  # type: ignore[attr-defined]
        return True


def install_request_id_filter() -> None:
    """Add the default request_id filter to the root logger."""
    root = logging.getLogger()
    for f in root.filters:
        if isinstance(f, _RequestIdFilter):
            return
    root.addFilter(_RequestIdFilter())
