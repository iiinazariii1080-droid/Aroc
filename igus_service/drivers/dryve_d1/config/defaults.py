"""Default timing values and policies.

These defaults are conservative and should work on typical LAN/VPN deployments.
Tune them per environment (Wi-Fi vs. wired, NAT vs. local, etc.).

Time units:
  - *_S are seconds (float)
  - *_MS are milliseconds (int)

Notes:
  - The Modbus gateway can close the port if keepalive is missing multiple times.
  - The session layer is responsible for keepalive scheduling and reconnection.
"""

# Transport / socket
DEFAULT_CONNECT_TIMEOUT_S: float = 3.0
DEFAULT_REQUEST_TIMEOUT_S: float = 1.5
DEFAULT_SOCKET_IDLE_TIMEOUT_S: float = 10.0  # if no traffic for too long, reconnect defensively

# Keepalive / heartbeat
DEFAULT_KEEPALIVE_INTERVAL_S: float = 1.0
DEFAULT_KEEPALIVE_MISS_LIMIT: int = 3  # "3 misses" is common per gateway documentation

# Telemetry polling
# Separate "status poll" (minimal reads to track state machine) vs broader telemetry.
DEFAULT_STATUS_POLL_S: float = 0.25
DEFAULT_TELEMETRY_POLL_S: float = 0.5

# Jog
DEFAULT_JOG_TTL_MS: int = 200  # front-end should refresh more frequently than this


def default_driver_config(
  *,
  host: str,
  port: int = 502,
  unit_id: int = 1,
):
  """Build a default driver config with conservative timeouts.

  Import locally to avoid circular import between defaults and models.
  """

  from ..api.drive import DryveD1Config
  from .models import ConnectionConfig, DriveConfig

  drive_cfg = DriveConfig(connection=ConnectionConfig(host=host, port=port, unit_id=unit_id))
  return DryveD1Config(drive=drive_cfg)
