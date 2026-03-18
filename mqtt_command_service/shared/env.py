"""Unified environment-variable parsing helpers.

Single source of truth — all services import from here instead of
maintaining local copies with divergent edge-case behaviour.
"""

import logging
import os
import re
from typing import Any

from shared.constants import MQTT_DEFAULT_PORT, MQTT_TLS_PORT

logger = logging.getLogger(__name__)

# MQTT topic-safe: alphanumerics, hyphens, underscores, dots only.
_ROBOT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def env(key: str, default: str) -> str:
    """Return env var *key* as a string, or *default*."""
    return os.environ.get(key, default)


def env_float(key: str, default: float) -> float:
    """Return env var *key* as a float; raise ValueError on bad input."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Invalid float value for {key}: {raw!r}") from None


def env_int(key: str, default: int) -> int:
    """Return env var *key* as an int; raise ValueError on bad input."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Invalid int value for {key}: {raw!r}") from None


def parse_bool(value: Any, default: bool = False) -> bool:
    """Parse an arbitrary value as a bool.

    Recognises true/1/yes/on and false/0/no/off (case-insensitive strings),
    plus native ``bool`` and ``int``.  Returns *default* for unrecognised values.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    normalised = str(value).strip().lower()
    if normalised in ("true", "1", "yes", "on"):
        return True
    if normalised in ("false", "0", "no", "off", ""):
        return False
    return default


def env_bool(key: str, default: bool) -> bool:
    """Return env var *key* as a bool.

    Recognises true/1/yes/on and false/0/no/off (case-insensitive).
    Logs a warning and returns *default* for unrecognised values.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    result = parse_bool(raw, default)
    if result == default and raw.strip().lower() not in (
        "true",
        "1",
        "yes",
        "on",
        "false",
        "0",
        "no",
        "off",
        "",
    ):
        logger.warning("Unrecognised boolean value for %s: %r — falling back to %s", key, raw, default)
    return result


def resolve_mqtt_port(port: int, use_tls: bool) -> int:
    """Auto-switch between plain and TLS default ports.

    If the user explicitly chose a non-default port, leave it alone.
    """
    if use_tls and port == MQTT_DEFAULT_PORT:
        return MQTT_TLS_PORT
    if not use_tls and port == MQTT_TLS_PORT:
        return MQTT_DEFAULT_PORT
    return port


def validate_robot_id(robot_id: str) -> str:
    """Ensure *robot_id* is safe for use in MQTT topics.

    Raises ValueError if the value is empty or contains characters
    that could corrupt topic patterns (MQTT wildcards, slashes, etc.).
    """
    if not robot_id or not _ROBOT_ID_RE.match(robot_id):
        raise ValueError(
            f"Invalid robot_id {robot_id!r}: must be non-empty and contain "
            "only alphanumerics, hyphens, underscores, or dots"
        )
    return robot_id
