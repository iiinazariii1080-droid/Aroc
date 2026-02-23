"""Transport session: serialization, keepalive, retry/reconnect.

Key properties:
- Exactly one in-flight request at a time (lock).
- Reconnect on transport I/O failures.
- Optional keepalive thread to satisfy networks/drives that expect periodic traffic.

The session exchanges raw ADUs (bytes). Protocol validation is outside of this module.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..config.runtime_policy import allow_tid_mismatch
from .clock import monotonic_s, sleep_s
from .modbus_tcp_client import ModbusTcpClient, TcpConfig
from .retry import RetryBudget, RetryPolicy


class TransactionIdGenerator:
    """Thread-safe Modbus transaction-id generator (0..65535)."""

    def __init__(self, start: int = 1) -> None:
        self._lock = threading.Lock()
        self._next = int(start) & 0xFFFF

    def next(self) -> int:
        with self._lock:
            tid = self._next
            self._next = (self._next + 1) & 0xFFFF
            # Avoid 0 if you want to keep it reserved for tests; optional.
            if self._next == 0:
                self._next = 1
            return tid

    def align(self, next_value: int) -> None:
        """Set next transaction id (thread-safe)."""
        with self._lock:
            self._next = int(next_value) & 0xFFFF
            if self._next == 0:
                self._next = 1


@dataclass(frozen=True, slots=True)
class KeepAliveConfig:
    enabled: bool = False
    interval_s: float = 0.50
    # build a raw ADU to send; called from keepalive thread
    build_adu: Callable[[], bytes] | None = None
    # if True, keepalive errors will trigger reconnect attempts
    reconnect_on_error: bool = True


class ModbusSession:
    """High-level Modbus TCP session for raw ADU exchange."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 502,
        connect_timeout_s: float = 3.0,
        io_timeout_s: float = 2.0,
        retry_policy: RetryPolicy | None = None,
        keepalive: KeepAliveConfig | None = None,
        on_reconnect: Callable[[], None] | None = None,  # B4: Callback called on successful reconnect
        logger=None,
    ) -> None:
        self._cfg = TcpConfig(
            host=host,
            port=port,
            connect_timeout_s=connect_timeout_s,
            io_timeout_s=io_timeout_s,
        )
        self._log = logger
        self._client = ModbusTcpClient(self._cfg, logger=logger)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._keepalive_cfg = keepalive or KeepAliveConfig(enabled=False)
        self._keepalive_thread: threading.Thread | None = None
        self._retry_policy = retry_policy or RetryPolicy()
        self._last_activity_s = monotonic_s()
        self._tid = TransactionIdGenerator()
        self._on_reconnect = on_reconnect  # B4: Reconnect safety callback

    # --------------------
    # Lifecycle
    # --------------------
    def connect(self) -> None:
        """Ensure the underlying socket is connected."""
        was_connected = self._client.is_connected
        if was_connected:
            return
        self._client.connect()
        self._last_activity_s = monotonic_s()

        # B4: Call reconnect callback if this was a reconnect (was not connected, now connected)
        if self._on_reconnect is not None:
            try:
                self._on_reconnect()
            except Exception:
                # Don't let reconnect callback block connection
                pass

        if self._keepalive_cfg.enabled and self._keepalive_thread is None:
            self._start_keepalive_thread()

    def close(self) -> None:
        """Stop keepalive and close socket."""
        self._stop_event.set()
        t = self._keepalive_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._keepalive_thread = None
        self._client.close()

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    def next_transaction_id(self) -> int:
        return self._tid.next()

    # --------------------
    # Core I/O
    # --------------------
    def transceive(self, adu: bytes, *, deadline_s: float | None = None) -> bytes:
        """Send request and receive response (raw bytes) with retry/reconnect."""
        budget = RetryBudget(policy=self._retry_policy, deadline_s=deadline_s)

        while True:
            budget.attempts += 1
            try:
                with self._lock:
                    self.connect()
                    resp = self._client.transceive(adu)
                    self._last_activity_s = monotonic_s()

                    # Align transaction ID generator if remote uses different TID
                    try:
                        allow = allow_tid_mismatch()
                        if allow and len(resp) >= 2 and len(adu) >= 2:
                            resp_tid = (resp[0] << 8) | resp[1]
                            req_tid = (adu[0] << 8) | adu[1]
                            if resp_tid != req_tid:
                                # Set next tid to resp_tid + 1 to reduce future mismatches
                                next_tid = (resp_tid + 1) & 0xFFFF
                                self._tid.align(next_tid)
                                logging.getLogger(__name__).debug(
                                    "Aligned transaction id generator to %d (resp=%d, req=%d)",
                                    next_tid,
                                    resp_tid,
                                    req_tid,
                                )
                    except Exception:
                        # Never let alignment errors break transceive
                        logging.getLogger(__name__).exception("Error aligning TID generator")

                    return resp
            except budget.policy.retry_on:
                # Transport failure: close and retry.
                # close() is inside the lock to prevent racing with other threads
                with self._lock:
                    self._client.close()
                if not budget.can_retry():
                    raise
                budget.sleep_before_next()

    # --------------------
    # Keepalive
    # --------------------
    def _start_keepalive_thread(self) -> None:
        if not self._keepalive_cfg.enabled:
            return
        if self._keepalive_cfg.build_adu is None:
            raise ValueError("KeepAliveConfig.enabled requires build_adu callable")

        self._stop_event.clear()
        t = threading.Thread(target=self._keepalive_loop, name="dryve-modbus-keepalive", daemon=True)
        self._keepalive_thread = t
        t.start()

    def _keepalive_loop(self) -> None:
        interval = max(0.05, float(self._keepalive_cfg.interval_s))
        build = self._keepalive_cfg.build_adu
        assert build is not None

        while not self._stop_event.is_set():
            # If there is recent activity, we still send keepalive on schedule.
            # Some stacks expect periodic 'heartbeat' regardless of traffic burst patterns.
            try:
                adu = build()
                # Use a small per-keepalive deadline; don't block indefinitely.
                deadline = monotonic_s() + max(0.2, interval)
                self.transceive(adu, deadline_s=deadline)
            except Exception:
                if self._keepalive_cfg.reconnect_on_error:
                    with self._lock:
                        try:
                            self._client.close()
                        except Exception:
                            pass
                # Keepalive must not crash the process; continue loop.
            finally:
                sleep_s(interval)
