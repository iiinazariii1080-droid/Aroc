"""IT-04: Retry Exhaustion → Clean Error Propagation.

Verifies that ModbusSession retry budget exhaustion produces a clean exception,
not a hang or deadlock. Uses FakeClient that always fails.

Boundary: C (Driver ↔ Modbus transport)
Risk: R-02 — executor starvation under retry storm
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from drivers.dryve_d1.transport.session import ModbusSession, KeepAliveConfig
from drivers.dryve_d1.transport.retry import RetryPolicy


class AlwaysFailClient:
    """Modbus TCP client that always raises ConnectionError."""

    is_connected = True

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def transceive(self, adu: bytes) -> bytes:
        raise ConnectionError("Simulated Modbus failure")


def _make_session(*, max_attempts: int = 3, base_delay_s: float = 0.01) -> ModbusSession:
    session = ModbusSession.__new__(ModbusSession)
    session._cfg = MagicMock()
    session._log = None
    session._client = AlwaysFailClient()
    session._lock = threading.Lock()
    session._stop_event = threading.Event()
    session._keepalive_cfg = KeepAliveConfig(enabled=False)
    session._keepalive_thread = None
    session._retry_policy = RetryPolicy(
        max_attempts=max_attempts,
        base_delay_s=base_delay_s,
        max_delay_s=0.05,
        jitter_fraction=0.0,
    )
    session._last_activity_s = time.monotonic()
    session._tid = MagicMock()
    session._tid.next.return_value = 1
    session._on_reconnect = None
    session._ever_connected = True
    session._keepalive_skipped = 0
    return session


def test_retry_exhaustion_raises_connection_error() -> None:
    """After max_attempts retries, transceive raises the last ConnectionError."""
    session = _make_session(max_attempts=3, base_delay_s=0.01)
    adu = b"\x00\x01\x00\x00\x00\x06\x01\x03\x60\x41\x00\x01"

    with pytest.raises(ConnectionError, match="Simulated Modbus failure"):
        session.transceive(adu)


def test_retry_exhaustion_completes_within_timeout() -> None:
    """Retry exhaustion must complete, not hang indefinitely."""
    session = _make_session(max_attempts=3, base_delay_s=0.01)
    adu = b"\x00\x01\x00\x00\x00\x06\x01\x03\x60\x41\x00\x01"

    t0 = time.monotonic()
    with pytest.raises(ConnectionError):
        session.transceive(adu)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"Retry exhaustion took {elapsed:.1f}s — possible deadlock"


def test_single_attempt_raises_immediately() -> None:
    """With max_attempts=1, no retry occurs — fails on first call."""
    session = _make_session(max_attempts=1, base_delay_s=0.01)
    adu = b"\x00\x01\x00\x00\x00\x06\x01\x03\x60\x41\x00\x01"

    t0 = time.monotonic()
    with pytest.raises(ConnectionError):
        session.transceive(adu)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"Single attempt took {elapsed:.1f}s"


def test_client_closed_after_each_failure() -> None:
    """After each transceive failure, session closes the client socket."""
    session = _make_session(max_attempts=2, base_delay_s=0.01)
    client = session._client
    adu = b"\x00\x01\x00\x00\x00\x06\x01\x03\x60\x41\x00\x01"

    with pytest.raises(ConnectionError):
        session.transceive(adu)

    # Client should have been closed during retry cleanup
    assert not client.is_connected, "Client should be closed after retry exhaustion"
