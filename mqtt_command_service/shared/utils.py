"""Shared utility functions."""

import json
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from shared.constants import TASK_TERMINAL_STATES

logger = logging.getLogger(__name__)


def safe_json_loads(payload: str) -> tuple[Any, str | None]:
    """Safely parse JSON payload with error reporting."""
    try:
        return json.loads(payload), None
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error at line {e.lineno}, column {e.colno}: {e.msg}"
        logger.debug("JSON parsing failed: %s", error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error parsing JSON: {e!s}"
        logger.warning("Unexpected JSON parsing error: %s", error_msg, exc_info=True)
        return None, error_msg


def is_task_done(body: Any) -> bool:
    """Return True only when the task has reached a known terminal state."""
    if not isinstance(body, dict):
        return False

    state = str(body.get("state", "") or body.get("status", "")).lower()
    if state in TASK_TERMINAL_STATES:
        return True

    return body.get("success") is False


# ---- Singleton factory ------------------------------------------------


def singleton_factory[T](factory: type[T] | Any) -> Any:
    """Create a thread-safe singleton accessor for *factory*.

    Returns a callable ``get_instance()`` that lazily creates and caches
    the singleton returned by ``factory()``.  Guarantees exactly-once
    execution of *factory* even under concurrent access.

    Usage::

        _get_settings = singleton_factory(MySettings.from_env)

        def get_settings() -> MySettings:
            return _get_settings()
    """
    _sentinel = object()
    instance: Any = _sentinel
    lock = threading.Lock()

    def _get() -> T:
        nonlocal instance
        if instance is not _sentinel:
            return instance
        with lock:
            if instance is _sentinel:
                instance = factory()
        return instance

    return _get


# ---- ISO timestamp ----------------------------------------------------


def serialize_mqtt_payload(payload: dict[str, Any]) -> tuple[str, bytes] | None:
    """Serialize payload to compact JSON and check MQTT size limit.

    Returns (json_str, encoded_bytes) on success, None if too large.
    """
    from shared.constants import MAX_MQTT_PAYLOAD_SIZE

    message = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    encoded = message.encode("utf-8")
    if len(encoded) > MAX_MQTT_PAYLOAD_SIZE:
        return None
    return message, encoded


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Single source of truth — replaces identical helpers scattered across
    mqtt_publisher.py, telemetry_payload.py, and bridge.py.
    """
    return datetime.now(UTC).isoformat()
