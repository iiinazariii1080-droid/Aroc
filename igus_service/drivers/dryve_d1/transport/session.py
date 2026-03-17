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
    """Thread-safe Modbus transaction-id generator.

    The dryve D1 only echoes the lower 8 bits of the transaction ID in
    responses, so by default we wrap at 255 (``max_value=0xFF``).  This
    keeps request and response TIDs identical and avoids spurious
    ``ResponseMismatch`` errors once the counter would exceed 255.
    """

    def __init__(self, start: int = 1, *, max_value: int = 0xFF) -> None:
        self._lock = threading.Lock()
        self._max = int(max_value)
        self._next = int(start) % (self._max + 1) or 1

    def next(self) -> int:
        with self._lock:
            tid = self._next
            self._next = (self._next + 1) % (self._max + 1)
            if self._next == 0:
                self._next = 1
            return tid

    def align(self, next_value: int) -> None:
        """Set next transaction id (thread-safe)."""
        with self._lock:
            self._next = int(next_value) % (self._max + 1)
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
    """High-level Modbus TCP session for raw ADU exchange.

    .. warning:: Blocking I/O in asyncio context
        ``transceive()`` and ``connect()`` are synchronous (blocking).
        The driver's async layer wraps calls via ``asyncio.to_thread()``
        to avoid blocking the event loop.  Keep timeout values low
        (DRYVE_CONNECT_TIMEOUT_S <= 3 s, DRYVE_REQUEST_TIMEOUT_S <= 1.5 s)
        to limit worst-case thread-pool starvation during reconnect sequences.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 502,
        connect_timeout_s: float = 3.0,
        io_timeout_s: float = 2.0,
        retry_policy: RetryPolicy | None = None,
        keepalive: KeepAliveConfig | None = None,
        on_reconnect: Callable[[], None] | None = None,
        tid_gen: TransactionIdGenerator | None = None,
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
        self._tid = tid_gen if tid_gen is not None else TransactionIdGenerator()
        self._on_reconnect = on_reconnect  # called on RE-connect only (not initial)
        self._ever_connected: bool = False
        self._keepalive_skipped: int = 0
        self._suppress_keepalive_until: float = 0.0

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

        # Only fire on_reconnect for RE-connections, not the initial connect.
        if self._ever_connected and self._on_reconnect is not None:
            try:
                self._on_reconnect()
            except Exception:
                logging.getLogger(__name__).warning(
                    "on_reconnect callback failed", exc_info=True,
                )
        self._ever_connected = True

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

    def suppress_keepalive(self, duration_s: float = 0.5) -> None:
        """Suppress keepalive I/O for *duration_s* seconds.

        Used before critical controlword writes (e.g. disable_voltage) where
        concurrent Modbus reads from the keepalive thread can prevent the
        dryve D1 firmware from processing the state transition.
        """
        new_deadline = monotonic_s() + duration_s
        self._suppress_keepalive_until = max(self._suppress_keepalive_until, new_deadline)

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
                if self._stop_event.is_set():
                    raise ConnectionError("Session closed during retry")
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

        # Stop old thread if still running to prevent race on shared socket
        old = self._keepalive_thread
        if old is not None and old.is_alive():
            self._stop_event.set()
            old.join(timeout=2.0)

        self._stop_event.clear()
        t = threading.Thread(target=self._keepalive_loop, name="dryve-modbus-keepalive", daemon=True)
        self._keepalive_thread = t
        t.start()

    def _keepalive_loop(self) -> None:
        """Send periodic keepalive packets using single-attempt I/O.

        Uses ``self._client.transceive()`` directly instead of
        ``self.transceive()`` to avoid the ``RetryBudget`` retry/reconnect
        loop.  This prevents the keepalive thread from holding ``self._lock``
        for extended periods during connection loss, which would starve
        ``asyncio.to_thread(session.transceive, ...)`` callers and exhaust
        the default thread pool.
        """
        interval = max(0.05, float(self._keepalive_cfg.interval_s))
        build = self._keepalive_cfg.build_adu
        if build is None:
            raise ValueError("KeepAliveConfig.build_adu must not be None when keepalive is enabled")
        log = logging.getLogger(__name__)

        while not self._stop_event.is_set():
            try:
                # Honor suppression window (e.g. during disable_voltage)
                if monotonic_s() < self._suppress_keepalive_until:
                    self._stop_event.wait(timeout=0.05)
                    continue
                adu = build()
                if not self._lock.acquire(timeout=0.5):
                    self._keepalive_skipped += 1
                    log.debug("Keepalive skipped (%d total): lock busy", self._keepalive_skipped)
                    continue
                try:
                    if self._client.is_connected:
                        try:
                            self._client.transceive(adu)
                            self._last_activity_s = monotonic_s()
                        except Exception:
                            log.warning(
                                "Keepalive I/O failed; closing socket for reconnect"
                            )
                            try:
                                self._client.close()
                            except Exception:
                                pass
                finally:
                    self._lock.release()
            except Exception as _ka_exc:
                log.warning("Keepalive cycle error: %s", _ka_exc, exc_info=True)
            finally:
                # Use Event.wait instead of blocking sleep so that close()
                # can wake us instantly by setting _stop_event.
                self._stop_event.wait(timeout=interval)
