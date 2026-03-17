"""dryve_d1 - igus dryve D1 Modbus TCP Gateway driver (v2).

Public entry points:
- DryveD1 (async facade)
- TelemetryPoller / DriveSnapshot (telemetry helpers)
"""

# Public API
from .api.drive import DryveD1

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
    "TelemetryConfig",
    "TelemetryPoller",
    "__version__",
]
