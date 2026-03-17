"""Application configuration — frozen Settings loaded lazily from environment.

All env-var reading is deferred to ``Settings.from_env()`` which runs on first
call to ``get_settings()``.  No ``os.getenv`` at module import time.
"""

from __future__ import annotations

import datetime
import email.utils
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from drivers.dryve_d1.api.drive import DryveD1Config

from app.env_loader import load_env_file


def _opt_int(key: str) -> int | None:
    """Return ``int(os.getenv(key))`` or ``None`` if unset."""
    raw = os.getenv(key)
    return None if raw is None else int(raw)


def _bool_env(key: str, default: str) -> bool:
    return os.getenv(key, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings populated from environment variables.

    Create via ``Settings.from_env()`` (production) or by passing explicit
    values (tests).  Never mutated after construction.
    """

    # -- DryveD1 connection ---------------------------------------------------
    dryve_host: str = "127.0.0.1"
    dryve_port: int = 502
    dryve_unit_id: int = 0

    # Runtime policy
    dryve_allow_tid_mismatch: bool = True
    dryve_allow_unit_id_wildcard: bool = True

    # Connection timeouts
    dryve_connect_timeout_s: float = 3.0
    dryve_request_timeout_s: float = 1.5
    dryve_socket_idle_timeout_s: float = 10.0

    # Retry policy
    dryve_retry_max_attempts: int | None = None
    dryve_retry_base_delay_s: float = 0.25
    dryve_retry_max_delay_s: float = 5.0
    dryve_retry_jitter_s: float = 0.1

    # Poll rates
    dryve_telemetry_poll_s: float = 0.2
    dryve_status_poll_s: float = 0.2
    dryve_keepalive_interval_s: float = 1.0
    dryve_keepalive_miss_limit: int = 3

    # Motion limits (None = no limit)
    dryve_max_abs_position: int | None = None
    dryve_max_abs_velocity: int | None = None
    dryve_max_abs_accel: int | None = None
    dryve_max_abs_decel: int | None = None

    # Position limits (hardware-enforced in drive)
    dryve_min_position_limit: int = 0
    dryve_max_position_limit: int = 120000

    # Jog
    dryve_jog_ttl_ms: int = 200
    dryve_jog_default_speed: float = 2000.0

    # Status event throttle
    dryve_status_event_throttle_s: float = 0.5

    # Health scoring weights
    dryve_health_weight_disconnected: int = 50
    dryve_health_weight_startup_error: int = 30
    dryve_health_weight_telemetry_stale: int = 20
    dryve_health_weight_fault_active: int = 30
    dryve_health_weight_callback_error_max: int = 20
    dryve_health_readiness_threshold: int = 50

    # Legacy API
    legacy_max_velocity: int = 10000
    legacy_max_acceleration: int = 5000
    legacy_api_deprecation: bool = True
    legacy_api_sunset: str = "Wed, 30 Sep 2026 23:59:59 GMT"
    legacy_api_docs_link: str = "/docs"
    legacy_api_phase: str = "deprecated"
    legacy_api_successor_path: str = "/drive"

    # Build profile
    build_profile: str = "production"

    @classmethod
    def from_env(cls) -> Settings:
        """Read all settings from environment variables (with .env fallback).

        Call ``load_env_file()`` first so that .env values are visible.
        """
        load_env_file()

        from drivers.dryve_d1.config.runtime_policy import (
            default_tid_mismatch_tolerance,
            default_unit_id_wildcard_tolerance,
        )

        return cls(
            # Connection
            dryve_host=os.getenv("DRYVE_HOST", os.getenv("IGUS_MOTOR_IP", "127.0.0.1")),
            dryve_port=int(os.getenv("DRYVE_PORT", os.getenv("IGUS_MOTOR_PORT", "502"))),
            dryve_unit_id=int(os.getenv("DRYVE_UNIT_ID", "0")),
            # Runtime policy
            dryve_allow_tid_mismatch=_bool_env(
                "DRYVE_ALLOW_TID_MISMATCH",
                "1" if default_tid_mismatch_tolerance() else "0",
            ),
            dryve_allow_unit_id_wildcard=_bool_env(
                "DRYVE_ALLOW_UNIT_ID_WILDCARD",
                "1" if default_unit_id_wildcard_tolerance() else "0",
            ),
            # Timeouts
            dryve_connect_timeout_s=float(os.getenv("DRYVE_CONNECT_TIMEOUT_S", "3.0")),
            dryve_request_timeout_s=float(os.getenv("DRYVE_REQUEST_TIMEOUT_S", "1.5")),
            dryve_socket_idle_timeout_s=float(os.getenv("DRYVE_SOCKET_IDLE_TIMEOUT_S", "10.0")),
            # Retry
            dryve_retry_max_attempts=_opt_int("DRYVE_RETRY_MAX_ATTEMPTS"),
            dryve_retry_base_delay_s=float(os.getenv("DRYVE_RETRY_BASE_DELAY_S", "0.25")),
            dryve_retry_max_delay_s=float(os.getenv("DRYVE_RETRY_MAX_DELAY_S", "5.0")),
            dryve_retry_jitter_s=float(os.getenv("DRYVE_RETRY_JITTER_S", "0.1")),
            # Poll rates
            dryve_telemetry_poll_s=float(os.getenv("DRYVE_TELEMETRY_POLL_S", "0.2")),
            dryve_status_poll_s=float(os.getenv("DRYVE_STATUS_POLL_S", "0.2")),
            dryve_keepalive_interval_s=float(os.getenv("DRYVE_KEEPALIVE_INTERVAL_S", "1.0")),
            dryve_keepalive_miss_limit=int(os.getenv("DRYVE_KEEPALIVE_MISS_LIMIT", "3")),
            # Motion limits
            dryve_max_abs_position=_opt_int("DRYVE_MAX_ABS_POSITION"),
            dryve_max_abs_velocity=_opt_int("DRYVE_MAX_ABS_VELOCITY"),
            dryve_max_abs_accel=_opt_int("DRYVE_MAX_ABS_ACCEL"),
            dryve_max_abs_decel=_opt_int("DRYVE_MAX_ABS_DECEL"),
            dryve_min_position_limit=int(os.getenv("DRYVE_MIN_POSITION_LIMIT", "0")),
            dryve_max_position_limit=int(os.getenv("DRYVE_MAX_POSITION_LIMIT", "120000")),
            # Jog
            dryve_jog_ttl_ms=int(os.getenv("DRYVE_JOG_TTL_MS", "200")),
            dryve_jog_default_speed=float(os.getenv("DRYVE_JOG_DEFAULT_SPEED", "2000")),
            # Status event throttle
            dryve_status_event_throttle_s=float(os.getenv("DRYVE_STATUS_EVENT_THROTTLE_S", "0.5")),
            # Health weights
            dryve_health_weight_disconnected=int(os.getenv("DRYVE_HEALTH_WEIGHT_DISCONNECTED", "50")),
            dryve_health_weight_startup_error=int(os.getenv("DRYVE_HEALTH_WEIGHT_STARTUP_ERROR", "30")),
            dryve_health_weight_telemetry_stale=int(os.getenv("DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE", "20")),
            dryve_health_weight_fault_active=int(os.getenv("DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE", "30")),
            dryve_health_weight_callback_error_max=int(os.getenv("DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX", "20")),
            dryve_health_readiness_threshold=int(os.getenv("DRYVE_HEALTH_READINESS_THRESHOLD", "50")),
            # Legacy API
            legacy_max_velocity=int(os.getenv("LEGACY_MAX_VELOCITY", "10000")),
            legacy_max_acceleration=int(os.getenv("LEGACY_MAX_ACCELERATION", "5000")),
            legacy_api_deprecation=_bool_env("LEGACY_API_DEPRECATION", "true"),
            legacy_api_sunset=os.getenv("LEGACY_API_SUNSET", "Wed, 30 Sep 2026 23:59:59 GMT"),
            legacy_api_docs_link=os.getenv("LEGACY_API_DOCS_LINK", "/docs"),
            legacy_api_phase=os.getenv("LEGACY_API_PHASE", "deprecated").lower(),
            legacy_api_successor_path=os.getenv("LEGACY_API_SUCCESSOR_PATH", "/drive"),
            # Build
            build_profile=os.getenv("BUILD_PROFILE", "production"),
        )

    def to_info_dict(self) -> dict[str, Any]:
        """Build the info dict stored on ``app.state.settings`` for /ready and /info."""
        from app.version import DRIVER_VERSION as driver_version

        return {
            "DRYVE_HOST": self.dryve_host,
            "DRYVE_PORT": self.dryve_port,
            "DRYVE_UNIT_ID": self.dryve_unit_id,
            "DRIVER_VERSION": driver_version,
            "DRYVE_TELEMETRY_POLL_S": self.dryve_telemetry_poll_s,
            "DRYVE_STATUS_POLL_S": self.dryve_status_poll_s,
            "DRYVE_HEALTH_WEIGHT_DISCONNECTED": self.dryve_health_weight_disconnected,
            "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR": self.dryve_health_weight_startup_error,
            "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE": self.dryve_health_weight_telemetry_stale,
            "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE": self.dryve_health_weight_fault_active,
            "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX": self.dryve_health_weight_callback_error_max,
            "DRYVE_HEALTH_READINESS_THRESHOLD": self.dryve_health_readiness_threshold,
        }


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings instance, creating it on first call."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    """Reset cached settings — for tests that need different env configurations."""
    global _settings  # noqa: PLW0603
    _settings = None


# ---------------------------------------------------------------------------
# Runtime helpers (kept as standalone functions for explicit call from startup)
# ---------------------------------------------------------------------------


def get_legacy_api_phase() -> str:
    """Return validated legacy API phase string (deprecated/sunset/removed).

    Runtime helper (not pure configuration): reads from Settings and performs
    date validation.  Lives here because it directly accesses the Settings
    singleton defined in this module.
    """
    _log = logging.getLogger(__name__)
    s = get_settings()

    phase = str(s.legacy_api_phase or "deprecated").lower()
    if phase not in {"deprecated", "sunset", "removed"}:
        _log.warning(
            "Unknown LEGACY_API_PHASE=%r — falling back to 'deprecated'. "
            "Valid values: deprecated, sunset, removed.",
            phase,
        )
        phase = "deprecated"

    if phase in ("deprecated", "sunset"):
        try:
            sunset_dt = email.utils.parsedate_to_datetime(s.legacy_api_sunset)
            if sunset_dt < datetime.datetime.now(datetime.timezone.utc):
                _log.warning(
                    "LEGACY_API_SUNSET (%s) is in the past but LEGACY_API_PHASE=%r. "
                    "Update LEGACY_API_SUNSET env var or advance the phase to 'removed'.",
                    s.legacy_api_sunset,
                    phase,
                )
        except Exception:
            _log.warning(
                "LEGACY_API_SUNSET value %r is not a valid RFC 2822 date — sunset enforcement disabled.",
                s.legacy_api_sunset,
            )

    return phase


# ---------------------------------------------------------------------------
# Factory — builds DryveD1Config from Settings (single source of truth)
# ---------------------------------------------------------------------------

def create_dryve_config(s: Settings) -> "DryveD1Config":
    """Build a ``DryveD1Config`` from application Settings.

    Centralises the env → driver-config mapping that was previously spread
    across 40+ lines in ``state.py:startup()``.
    """
    from drivers.dryve_d1.api.drive import DryveD1Config
    from drivers.dryve_d1.config.models import (
        ConnectionConfig,
        DriveConfig,
        JogConfig,
        MotionLimits,
        PollRates,
        RetryPolicy,
    )

    return DryveD1Config(
        drive=DriveConfig(
            connection=ConnectionConfig(
                host=s.dryve_host,
                port=s.dryve_port,
                unit_id=s.dryve_unit_id,
                connect_timeout_s=s.dryve_connect_timeout_s,
                request_timeout_s=s.dryve_request_timeout_s,
                socket_idle_timeout_s=s.dryve_socket_idle_timeout_s,
            ),
            retry=RetryPolicy(
                max_attempts=s.dryve_retry_max_attempts,
                base_delay_s=s.dryve_retry_base_delay_s,
                max_delay_s=s.dryve_retry_max_delay_s,
                jitter_s=s.dryve_retry_jitter_s,
            ),
            poll=PollRates(
                telemetry_poll_s=s.dryve_telemetry_poll_s,
                status_poll_s=s.dryve_status_poll_s,
                keepalive_interval_s=s.dryve_keepalive_interval_s,
                keepalive_miss_limit=s.dryve_keepalive_miss_limit,
            ),
            limits=MotionLimits(
                max_abs_position=s.dryve_max_abs_position,
                max_abs_velocity=s.dryve_max_abs_velocity,
                max_abs_accel=s.dryve_max_abs_accel,
                max_abs_decel=s.dryve_max_abs_decel,
                min_position_limit=s.dryve_min_position_limit,
                max_position_limit=s.dryve_max_position_limit,
            ),
            jog=JogConfig(ttl_ms=s.dryve_jog_ttl_ms),
        ),
    )
