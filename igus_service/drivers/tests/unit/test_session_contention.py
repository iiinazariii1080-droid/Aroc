"""Tests for Modbus session lock contention: transceive workers vs keepalive.

Verifies that under concurrent load (3 workers + keepalive thread), the system
does not deadlock and the keepalive gracefully skips when the lock is held.
Uses real threading.Lock and threading.Thread — no mocks for contention paths.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from drivers.dryve_d1.transport.session import KeepAliveConfig, ModbusSession
from drivers.dryve_d1.transport.retry import RetryPolicy


class SlowFakeClient:
    """Fake Modbus TCP client with controllable I/O latency."""

    def __init__(self, *, latency_s: float = 0.1) -> None:
        self.is_connected = True
        self._latency_s = latency_s
        self._transceive_count = 0
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def transceive(self, adu: bytes) -> bytes:
        time.sleep(self._latency_s)
        with self._lock:
            self._transceive_count += 1
        # Return minimal valid MBAP response (echo TID + 6 bytes)
        return adu[:2] + b"\x00\x00\x00\x03\x01\x03\x00"

    @property
    def transceive_count(self) -> int:
        with self._lock:
            return self._transceive_count


def _make_session(
    *,
    latency_s: float = 0.1,
    keepalive_interval_s: float = 0.05,
) -> tuple[ModbusSession, SlowFakeClient]:
    """Create a session with a SlowFakeClient and keepalive enabled."""
    client = SlowFakeClient(latency_s=latency_s)

    def _build_keepalive_adu() -> bytes:
        return b"\x00\x01\x00\x00\x00\x06\x01\x03\x60\x41\x00\x01"

    session = ModbusSession.__new__(ModbusSession)
    session._cfg = MagicMock()
    session._log = None
    session._client = client
    session._lock = threading.Lock()
    session._stop_event = threading.Event()
    session._keepalive_cfg = KeepAliveConfig(
        enabled=True,
        interval_s=keepalive_interval_s,
        build_adu=_build_keepalive_adu,
    )
    session._keepalive_thread = None
    session._retry_policy = RetryPolicy(max_attempts=1)
    session._last_activity_s = time.monotonic()
    session._tid = MagicMock()
    session._tid.next.return_value = 1
    session._on_reconnect = None
    session._ever_connected = True
    session._keepalive_skipped = 0
    session._suppress_keepalive_until = 0.0

    return session, client


def test_keepalive_skips_under_contention() -> None:
    """When main thread holds the lock, keepalive must skip (not deadlock)."""
    session, client = _make_session(latency_s=0.3, keepalive_interval_s=0.05)

    # Start keepalive thread and give it time to initialize
    session._start_keepalive_thread()
    time.sleep(0.1)

    # Hold the lock from main thread for 1.5s (keepalive acquire timeout is 0.5s,
    # so it should skip at least once during this hold)
    acquired = session._lock.acquire(timeout=1.0)
    assert acquired, "Failed to acquire lock"
    try:
        time.sleep(1.5)
    finally:
        session._lock.release()

    # Give keepalive a moment to run after lock release
    time.sleep(0.2)
    session.close()

    assert session._keepalive_skipped > 0, (
        f"Expected keepalive to skip at least once, got {session._keepalive_skipped}"
    )


def test_concurrent_transceive_no_deadlock() -> None:
    """3 concurrent transceive + keepalive thread must complete without deadlock."""
    session, client = _make_session(latency_s=0.05, keepalive_interval_s=0.1)

    session._start_keepalive_thread()

    results: list[bool] = []
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            adu = bytes([0x00, worker_id, 0x00, 0x00, 0x00, 0x06, 0x01, 0x03, 0x60, 0x41, 0x00, 0x01])
            resp = session.transceive(adu)
            results.append(len(resp) > 0)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()

    # All threads must complete within 5 seconds (no deadlock)
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), f"Thread {t.name} deadlocked"

    session.close()

    assert len(results) == 3, f"Expected 3 results, got {len(results)}: errors={errors}"
    assert all(results), "All transceive calls must return valid responses"


def test_keepalive_runs_after_workers_finish() -> None:
    """After workers finish, keepalive must resume normal operation."""
    session, client = _make_session(latency_s=0.02, keepalive_interval_s=0.05)

    session._start_keepalive_thread()

    # Run 3 workers
    threads = []
    for i in range(3):
        adu = bytes([0x00, i, 0x00, 0x00, 0x00, 0x06, 0x01, 0x03, 0x60, 0x41, 0x00, 0x01])
        t = threading.Thread(target=session.transceive, args=(adu,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=3.0)

    # Let keepalive run for a bit after workers finish
    count_before = client.transceive_count
    time.sleep(0.3)
    count_after = client.transceive_count

    session.close()

    # Keepalive must have made at least 1 successful transceive after workers
    assert count_after > count_before, (
        f"Keepalive did not resume: count went from {count_before} to {count_after}"
    )


def test_suppress_keepalive_blocks_io() -> None:
    """suppress_keepalive() should prevent keepalive I/O for the duration."""
    session, client = _make_session(latency_s=0.01, keepalive_interval_s=0.05)
    session._start_keepalive_thread()

    # Let keepalive run normally first
    time.sleep(0.2)
    count_before = client.transceive_count
    assert count_before > 0, "Keepalive should have run at least once"

    # Suppress for 0.5s
    session.suppress_keepalive(0.5)
    # Wait one keepalive cycle for in-flight I/O to finish
    time.sleep(0.1)
    count_at_suppress = client.transceive_count
    time.sleep(0.3)
    count_during_suppress = client.transceive_count

    # No new I/O should have happened during suppression
    assert count_during_suppress == count_at_suppress, (
        f"Keepalive sent I/O during suppression: {count_at_suppress} -> {count_during_suppress}"
    )

    # After suppression expires, keepalive should resume
    time.sleep(0.3)
    count_after = client.transceive_count

    session.close()

    assert count_after > count_during_suppress, (
        f"Keepalive did not resume after suppression: {count_during_suppress} -> {count_after}"
    )
