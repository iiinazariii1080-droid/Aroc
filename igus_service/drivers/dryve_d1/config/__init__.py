"""Configuration models and defaults for the dryve D1 driver (v2).

This package is intentionally small and dependency-light.
Higher layers should depend on the typed models here instead of hard-coding constants.
"""

from .defaults import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_JOG_TTL_MS,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_KEEPALIVE_MISS_LIMIT,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_SOCKET_IDLE_TIMEOUT_S,
    DEFAULT_STATUS_POLL_S,
    DEFAULT_TELEMETRY_POLL_S,
)
from .models import (
    ConnectionConfig,
    DriveConfig,
    JogConfig,
    MotionLimits,
    PollRates,
    RetryPolicy,
)

__all__ = [
    # defaults
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "DEFAULT_SOCKET_IDLE_TIMEOUT_S",
    "DEFAULT_KEEPALIVE_INTERVAL_S",
    "DEFAULT_KEEPALIVE_MISS_LIMIT",
    "DEFAULT_TELEMETRY_POLL_S",
    "DEFAULT_STATUS_POLL_S",
    "DEFAULT_JOG_TTL_MS",
    # models
    "ConnectionConfig",
    "RetryPolicy",
    "PollRates",
    "MotionLimits",
    "JogConfig",
    "DriveConfig",
]
