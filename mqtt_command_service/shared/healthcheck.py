"""File-based health check for non-HTTP services (mqtt-bridge, mqtt-telemetry).

Usage in main.py:
    from shared.healthcheck import start_heartbeat
    start_heartbeat(shutdown_event, is_healthy=lambda: bridge.mqtt_client.is_connected)

Docker HEALTHCHECK:
    CMD python -c "from shared.healthcheck import check; check()"
"""

import contextlib
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

HEALTHCHECK_FILE = "/tmp/healthy"
_HEALTHCHECK_PATH = Path(HEALTHCHECK_FILE)
HEARTBEAT_INTERVAL = 10  # seconds
MAX_AGE = 30  # seconds — if file is older than this, service is unhealthy


def touch() -> None:
    """Update the heartbeat file timestamp (mtime).

    Only the file's mtime matters — check() uses os.path.getmtime(),
    not the file contents.  Path.touch() is the simplest way to update it.
    """
    _HEALTHCHECK_PATH.touch()


def start_heartbeat(
    shutdown_event: threading.Event,
    interval: float = HEARTBEAT_INTERVAL,
    is_healthy: Callable[[], bool] | None = None,
) -> None:
    """Start a daemon thread that periodically touches the healthcheck file.

    Args:
        shutdown_event: Event that signals shutdown.
        interval: Seconds between heartbeats.
        is_healthy: Optional callable returning ``True`` when the service
            is healthy.  When provided, the heartbeat file is only updated
            while the callable returns ``True``; when it returns ``False``
            the file is removed so Docker marks the container unhealthy.
    """
    touch()

    def _loop() -> None:
        while not shutdown_event.is_set():
            if is_healthy is None or is_healthy():
                touch()
            else:
                # Remove file so Docker HEALTHCHECK fails promptly
                with contextlib.suppress(OSError):
                    os.unlink(HEALTHCHECK_FILE)
            shutdown_event.wait(timeout=interval)
        # Clean up on shutdown
        with contextlib.suppress(OSError):
            os.unlink(HEALTHCHECK_FILE)

    t = threading.Thread(target=_loop, daemon=True, name="healthcheck-heartbeat")
    t.start()


def check(max_age: float = MAX_AGE) -> None:
    """Check if the heartbeat file exists and is recent. Exits non-zero if unhealthy."""
    try:
        mtime = os.path.getmtime(HEALTHCHECK_FILE)
        age = time.time() - mtime
        if age > max_age:
            sys.exit(1)
    except OSError:
        sys.exit(1)
