"""Constants for the application."""
from enum import StrEnum
from pathlib import Path

# Certificate storage - fixed directory in application root
# Certificates are always stored in 'certs/' directory relative to application root
_APP_ROOT = Path(__file__).parent.resolve()
DEFAULT_CERT_STORAGE_DIR = _APP_ROOT / "certs"

# Fixed certificate file paths (always in certs/ directory)
CERT_CA_FILE = DEFAULT_CERT_STORAGE_DIR / "ca.crt"
CERT_CLIENT_CERT_FILE = DEFAULT_CERT_STORAGE_DIR / "client.crt"
CERT_CLIENT_KEY_FILE = DEFAULT_CERT_STORAGE_DIR / "client.key"

# File upload limits
MAX_CERT_SIZE = 1024 * 1024  # 1MB
MAX_BASE64_SIZE = 2 * 1024 * 1024  # 2MB (base64 encoded ≈ 1.5MB binary)

# File permissions
CERT_FILE_PERMISSIONS = 0o644
PRIVATE_KEY_PERMISSIONS = 0o600

# Allowed certificate extensions
ALLOWED_CERT_EXTENSIONS = {'.crt', '.pem', '.key', '.cer'}

# MQTT port constants
MQTT_DEFAULT_PORT = 1883  # Standard MQTT port (non-TLS)
MQTT_TLS_PORT = 8883  # Standard MQTT port (TLS/SSL)

# Default configuration (no hardcoded credentials — use env vars)
DEFAULT_MQTT_BROKER = ""  # Must be set via MQTT_BROKER env var or DB
DEFAULT_MQTT_USER = ""  # Must be set via MQTT_USER env var or DB
DEFAULT_MQTT_PASSWORD = ""  # Must be set via MQTT_PASS env var or DB
DEFAULT_ROBOT_ID = "robot-01"  # Override via ROBOT_ID env var

# MQTT payload size limit (conservative limit, MQTT spec allows up to 256MB)
MAX_MQTT_PAYLOAD_SIZE = 1024 * 1024  # 1MB

# Connection test timeout
DEFAULT_CONNECTION_TEST_TIMEOUT = 10.0  # seconds
HEALTH_CHECK_TIMEOUT = 5.0  # seconds (shorter timeout for health checks)

# Health check disk space requirement
HEALTH_CHECK_MIN_DISK_SPACE = 10 * 1024 * 1024  # 10MB (minimum for certificate uploads)

# MQTT connection timeouts
MQTT_CONNECTION_WAIT_TIMEOUT_SECONDS = 10.0  # Maximum time to wait for connection
MQTT_CONNECTION_CHECK_INTERVAL_SECONDS = 0.5  # Interval between connection checks
MQTT_CONNECTION_CHECK_ATTEMPTS = 20  # Number of connection check attempts

# Task polling timeouts
TASK_POLL_TIMEOUT_SECONDS = 3.0  # Maximum timeout for individual poll requests
COMMAND_HISTORY_TTL_SECONDS = 900.0  # 15 minutes TTL for command history
MAX_TASK_WATCHERS = 20  # Maximum concurrent task-watcher threads

# Log throttling
LOG_THROTTLE_INTERVAL_SECONDS = 60.0  # Log same message at most once per minute

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

# Message types
class MessageType(StrEnum):
    """Types of MQTT messages."""
    ACK = "ack"
    RESULT = "result"
    STATUS = "status"
    ERROR = "error"
    CONFIG_RESPONSE = "config_response"


# Status types
class StatusType(StrEnum):
    """Types of status messages."""
    SYSTEM = "system"
    CONNECTION = "connection"
    NAVIGATION = "navigation"


# Navigation states
class NavigationState(StrEnum):
    """States for navigation commands."""
    ACKNOWLEDGED = "acknowledged"
    DUPLICATE = "duplicate"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    WORKING = "working"


# Error types
class ErrorType(StrEnum):
    """Types of errors in error responses."""
    HTTP_ERROR = "http_error"
    COMMAND_ERROR = "command_error"
    ROUTING_ERROR = "routing_error"
    INVALID_JSON = "invalid_json"
    PROCESSING_ERROR = "processing_error"
    JSON_PARSE_ERROR = "json_parse_error"
    TLS_MISSING_FILES = "tls_missing_files"
    TLS_CONFIG_ERROR = "tls_config_error"

