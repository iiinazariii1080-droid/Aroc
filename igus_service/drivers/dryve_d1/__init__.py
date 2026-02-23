"""dryve_d1 - igus dryve D1 Modbus TCP Gateway driver (v2).

Public entry points:
- DryveD1 (async facade)
- MotorManager (optional multi-axis orchestration)
- TelemetryPoller / DriveSnapshot (telemetry helpers)
"""

# Public API
from .api.drive import DryveD1
from .api.motor_manager import MotorManager

# Exceptions
from .protocol.exceptions import MotionAborted

# Telemetry
from .telemetry.poller import TelemetryConfig, TelemetryPoller
from .telemetry.snapshots import DriveSnapshot
from .version import __version__

__all__ = [
    "DriveSnapshot",
    "DryveD1",
    "MotionAborted",
    "MotorManager",
    "TelemetryConfig",
    "TelemetryPoller",
    "__version__",
]
