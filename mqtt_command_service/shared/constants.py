"""Shared constants for all microservices."""

from enum import StrEnum
from pathlib import Path


# Well-known TCP ports for each micro-service.
SERVICE_PORT_IGUS: int = 8103
SERVICE_PORT_XARM: int = 8102
SERVICE_PORT_ROBOT: int = 8110
SERVICE_PORT_SYMOVO: int = 7905

HOST_LAN_IP_DEFAULT: str = "127.0.0.1"

# Certificate storage — each service mounts /certs from shared volume
DEFAULT_CERT_DIR = Path("/certs")


def cert_dir() -> Path:
    """Return the configured cert directory, respecting CERT_DIR env var.

    Single source of truth — used by all services (config-api, mqtt-bridge,
    mqtt-telemetry) to resolve certificate paths consistently.
    """
    import os

    return Path(os.environ.get("CERT_DIR", str(DEFAULT_CERT_DIR)))


def get_cert_path(filename: str) -> Path:
    """Return the full path for a certificate file, resolving CERT_DIR at call time."""
    return cert_dir() / filename


# File upload limits
MAX_CERT_SIZE = 1024 * 1024  # 1MB
MAX_BASE64_SIZE = 2 * 1024 * 1024  # 2MB
CERT_FILE_PERMISSIONS = 0o644
PRIVATE_KEY_PERMISSIONS = 0o600
ALLOWED_CERT_EXTENSIONS = {".crt", ".pem", ".key", ".cer"}

# MQTT port constants
MQTT_DEFAULT_PORT = 1883
MQTT_TLS_PORT = 8883

# Default configuration (no hardcoded credentials)
DEFAULT_MQTT_BROKER = ""
DEFAULT_MQTT_USER = ""
DEFAULT_MQTT_PASSWORD = ""
DEFAULT_ROBOT_ID = "robot-01"

# MQTT payload size limit
MAX_MQTT_PAYLOAD_SIZE = 1024 * 1024  # 1MB

# Connection test timeout
DEFAULT_CONNECTION_TEST_TIMEOUT = 10.0
HEALTH_CHECK_TIMEOUT = 5.0

# MQTT connection timeouts
MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS = 10.0
MQTT_CONNECTION_CHECK_INTERVAL_SECONDS = 0.5

# Task polling timeouts
TASK_POLL_TIMEOUT_SECONDS = 3.0
COMMAND_HISTORY_TTL_SECONDS = 900.0
MAX_TASK_WATCHERS = 20
MAX_TASK_RESULT_QUEUE = 100

# Bridge internal timing constants
SAFETY_HEARTBEAT_TIMEOUT_MIN = 60.0
SAFETY_HEARTBEAT_TIMEOUT_MULTIPLIER = 4
SAFETY_GATE_STARTUP_GRACE_SECONDS = 30.0
BRIDGE_DRAIN_TIMEOUT_SECONDS = 5.0
BRIDGE_DRAIN_POLL_INTERVAL_SECONDS = 0.25
BRIDGE_HTTP_EXECUTOR_MAX_WORKERS = 4

# Log throttle
ROBOT_ID_FALLBACK_WARN_INTERVAL = 60.0
# After this many consecutive fallback warnings, escalate to ERROR level.
ROBOT_ID_ESCALATE_AFTER = 5
LOG_THROTTLE_INTERVAL_SECONDS = 60.0

# Task status terminal states
TASK_TERMINAL_STATES = {
    "finished",
    "failed",
    "canceled",
    "cancelled",
    "error",
    "timeout",
    "done",
    "success",
}

# Subset of terminal states that indicate failure (not successful completion).
TASK_FAILURE_STATES = {"failed", "canceled", "cancelled", "error"}


class MessageType(StrEnum):
    ACK = "ack"
    RESULT = "result"
    STATUS = "status"
    ERROR = "error"


class StatusType(StrEnum):
    SYSTEM = "system"
    CONNECTION = "connection"
    NAVIGATION = "navigation"


class NavigationState(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    DUPLICATE = "duplicate"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


# Default service target for command dispatch
DEFAULT_COMMAND_SERVICE = "robot"


class ErrorType(StrEnum):
    HTTP_ERROR = "http_error"
    COMMAND_ERROR = "command_error"
    ROUTING_ERROR = "routing_error"
    INVALID_JSON = "invalid_json"
    PROCESSING_ERROR = "processing_error"
