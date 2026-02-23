from app.env_loader import load_env_file

load_env_file()

import os

from drivers.dryve_d1.config.runtime_policy import (
	default_tid_mismatch_tolerance,
	default_unit_id_wildcard_tolerance,
)

# Legacy compatibility
IGUS_MOTOR_IP = os.getenv("IGUS_MOTOR_IP", "82.165.177.194")
IGUS_MOTOR_PORT = int(os.getenv("IGUS_MOTOR_PORT", "502"))

# DryveD1 configuration
DRYVE_HOST = os.getenv("DRYVE_HOST", os.getenv("IGUS_MOTOR_IP", "82.165.177.194"))
DRYVE_PORT = int(os.getenv("DRYVE_PORT", os.getenv("IGUS_MOTOR_PORT", "502")))
DRYVE_UNIT_ID = int(os.getenv("DRYVE_UNIT_ID", "0"))  # Per igus dryve D1 Modbus TCP Gateway telegram spec: Unit Identifier is not used; send 0 by default.

# Runtime policy (fail-closed in production, tolerant in simulator/dev profiles)
os.environ.setdefault(
	"DRYVE_ALLOW_TID_MISMATCH",
	"1" if default_tid_mismatch_tolerance() else "0",
)
DRYVE_ALLOW_TID_MISMATCH = os.getenv("DRYVE_ALLOW_TID_MISMATCH", "1").lower() in ("1", "true", "yes")

os.environ.setdefault(
	"DRYVE_ALLOW_UNIT_ID_WILDCARD",
	"1" if default_unit_id_wildcard_tolerance() else "0",
)
DRYVE_ALLOW_UNIT_ID_WILDCARD = os.getenv("DRYVE_ALLOW_UNIT_ID_WILDCARD", "0").lower() in ("1", "true", "yes")

# Connection timeouts
DRYVE_CONNECT_TIMEOUT_S = float(os.getenv("DRYVE_CONNECT_TIMEOUT_S", "3.0"))
DRYVE_REQUEST_TIMEOUT_S = float(os.getenv("DRYVE_REQUEST_TIMEOUT_S", "1.5"))
DRYVE_SOCKET_IDLE_TIMEOUT_S = float(os.getenv("DRYVE_SOCKET_IDLE_TIMEOUT_S", "10.0"))

# Retry policy
_dryve_retry_max_attempts = os.getenv("DRYVE_RETRY_MAX_ATTEMPTS")
DRYVE_RETRY_MAX_ATTEMPTS = None if _dryve_retry_max_attempts is None else int(_dryve_retry_max_attempts)
DRYVE_RETRY_BASE_DELAY_S = float(os.getenv("DRYVE_RETRY_BASE_DELAY_S", "0.25"))
DRYVE_RETRY_MAX_DELAY_S = float(os.getenv("DRYVE_RETRY_MAX_DELAY_S", "5.0"))
DRYVE_RETRY_JITTER_S = float(os.getenv("DRYVE_RETRY_JITTER_S", "0.1"))

# Poll rates
DRYVE_TELEMETRY_POLL_S = float(os.getenv("DRYVE_TELEMETRY_POLL_S", "0.2"))
DRYVE_STATUS_POLL_S = float(os.getenv("DRYVE_STATUS_POLL_S", "0.2"))
DRYVE_KEEPALIVE_INTERVAL_S = float(os.getenv("DRYVE_KEEPALIVE_INTERVAL_S", "1.0"))
DRYVE_KEEPALIVE_MISS_LIMIT = int(os.getenv("DRYVE_KEEPALIVE_MISS_LIMIT", "3"))

# Motion limits (optional, None = no limit)
_dryve_max_abs_position = os.getenv("DRYVE_MAX_ABS_POSITION")
DRYVE_MAX_ABS_POSITION = None if _dryve_max_abs_position is None else int(_dryve_max_abs_position)
_dryve_max_abs_velocity = os.getenv("DRYVE_MAX_ABS_VELOCITY")
DRYVE_MAX_ABS_VELOCITY = None if _dryve_max_abs_velocity is None else int(_dryve_max_abs_velocity)
_dryve_max_abs_accel = os.getenv("DRYVE_MAX_ABS_ACCEL")
DRYVE_MAX_ABS_ACCEL = None if _dryve_max_abs_accel is None else int(_dryve_max_abs_accel)
_dryve_max_abs_decel = os.getenv("DRYVE_MAX_ABS_DECEL")
DRYVE_MAX_ABS_DECEL = None if _dryve_max_abs_decel is None else int(_dryve_max_abs_decel)

# Position limits (hardware-enforced in drive)
DRYVE_MIN_POSITION_LIMIT = int(os.getenv("DRYVE_MIN_POSITION_LIMIT", "0"))
DRYVE_MAX_POSITION_LIMIT = int(os.getenv("DRYVE_MAX_POSITION_LIMIT", "120000"))

# Jog config
DRYVE_JOG_TTL_MS = int(os.getenv("DRYVE_JOG_TTL_MS", "200"))
DRYVE_JOG_DEFAULT_SPEED = float(os.getenv("DRYVE_JOG_DEFAULT_SPEED", "2000"))

# Health scoring weights (0..100 score): configurable without code release
DRYVE_HEALTH_WEIGHT_DISCONNECTED = int(os.getenv("DRYVE_HEALTH_WEIGHT_DISCONNECTED", "50"))
DRYVE_HEALTH_WEIGHT_STARTUP_ERROR = int(os.getenv("DRYVE_HEALTH_WEIGHT_STARTUP_ERROR", "30"))
DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE = int(os.getenv("DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE", "20"))
DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE = int(os.getenv("DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE", "30"))
DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX = int(os.getenv("DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX", "20"))

# Legacy API deprecation policy
LEGACY_API_DEPRECATION = os.getenv("LEGACY_API_DEPRECATION", "true").lower() in ("1", "true", "yes")
LEGACY_API_SUNSET = os.getenv("LEGACY_API_SUNSET", "Wed, 30 Sep 2026 23:59:59 GMT")
LEGACY_API_DOCS_LINK = os.getenv("LEGACY_API_DOCS_LINK", "/docs")
LEGACY_API_PHASE = os.getenv("LEGACY_API_PHASE", "deprecated").lower()
LEGACY_API_SUCCESSOR_PATH = os.getenv("LEGACY_API_SUCCESSOR_PATH", "/drive")