"""DryveD1 facade — async API for dryve D1 over Modbus TCP Gateway.

The facade composes four mixins that own distinct concerns:

- ``OdAccessorMixin``    — low-level OD read/write via SDO
- ``IdleShutdownMixin``  — delayed disable_voltage after motion stops
- ``StatusQueriesMixin`` — cached-or-live status reads, is_moving, position limits
- ``MotionCommandsMixin``— move_to_position, jog, home, stop, fault_reset

DryveD1 itself owns lifecycle (connect/close), telemetry integration,
and reconnect safety.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..cia402.state_machine import CiA402StateMachine, StateMachineConfig
from ..config.models import DriveConfig as UserDriveConfig
from ..motion.homing import HomingConfig
from ..motion.jog import JogConfig as MotionJogConfig
from ..motion.jog import JogController
from ..motion.profile_position import ProfilePosition, ProfilePositionConfig
from ..motion.profile_velocity import ProfileVelocity, ProfileVelocityConfig
from ..od.indices import ODIndex
from ..od.statusword import decode_statusword, infer_cia402_state
from ..telemetry.poller import TelemetryConfig, TelemetryPoller
from ..telemetry.snapshots import DriveSnapshot
from ..transport import ModbusSession, TransactionIdGenerator
from ..transport.clock import monotonic_s
from ..transport.session import KeepAliveConfig

from .idle_shutdown import IdleShutdownMixin
from .motion_commands import MotionCommandsMixin
from .od_accessor import OdAccessorMixin
from .status_queries import StatusQueriesMixin

_LOGGER_MODBUS = logging.getLogger("dryve_d1.modbus")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DryveD1Config:
    """High-level configuration for the DryveD1 facade."""

    drive: UserDriveConfig
    state_machine: StateMachineConfig = StateMachineConfig()
    profile_position: ProfilePositionConfig = ProfilePositionConfig()
    profile_velocity: ProfileVelocityConfig = ProfileVelocityConfig()
    homing: HomingConfig = HomingConfig()
    jog: MotionJogConfig = MotionJogConfig()
    idle_shutdown_delay_s: float = 8.0
    velocity_threshold: int = 10  # drive units; below this the axis is "stationary"
    mode_settle_delay_s: float = 0.05  # delay after mode/controlword writes
    motion_precheck_delay_s: float = 0.1  # delay when stopping motion before a new command


class DryveD1(
    OdAccessorMixin,
    IdleShutdownMixin,
    StatusQueriesMixin,
    MotionCommandsMixin,
):
    """Async facade for dryve D1 over Modbus TCP Gateway.

    This object owns:
    - ModbusSession (socket + keepalive + serialized transceive)
    - SDOClient (serialization/parsing)
    - CiA402StateMachine runner
    - Motion helpers (profile position, velocity, homing, jog)

    Notes:
    - All OD reads/writes are performed via SDO over the gateway.
    - Networking is blocking under the hood; we offload to threads via `asyncio.to_thread`.
    """

    def __init__(self, *, config: DryveD1Config) -> None:
        if config is None:
            raise ValueError("config must not be None")

        self._cfg = config
        c = self._cfg.drive.connection

        from ..protocol import SDOClient
        self._sdo = SDOClient(unit_id=c.unit_id)

        self._session: ModbusSession | None = None

        # Higher-level helpers (created after connect)
        self._sm: CiA402StateMachine | None = None
        self._pp: ProfilePosition | None = None
        self._pv: ProfileVelocity | None = None
        self._homing = None
        self._jog: JogController | None = None

        # Dedicated thread pool for Modbus I/O — prevents starvation of the
        # default asyncio thread pool during retry/reconnect sequences.
        self._modbus_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="dryve-modbus",
        )
        retry = config.drive.retry
        max_attempts = int(retry.max_attempts) if retry.max_attempts is not None else 3
        backoff_budget = retry.base_delay_s * max_attempts * 2
        self._modbus_io_timeout_s: float = (
            config.drive.connection.request_timeout_s * max_attempts + backoff_budget + 1.0
        )

        # Reconnect safety
        self._reconnect_loop: asyncio.AbstractEventLoop | None = None

        # Telemetry
        self._telemetry_callback: Callable[[DriveSnapshot], None] | None = None
        self._telemetry_poller: TelemetryPoller | None = None

        # Idle shutdown state
        self._idle_shutdown_handle: asyncio.TimerHandle | None = None
        self._idle_shutdown_task: asyncio.Task[None] | None = None
        self._idle_shutdown_delay_s: float = config.idle_shutdown_delay_s

        # Abort event
        self._abort_event = asyncio.Event()

        # Reconnect stop debounce
        self._reconnect_stop_scheduled: bool = False

        # Atomic abort token
        self._abort_token: str = uuid.uuid4().hex

    # -----------------------------
    # Lifecycle
    # -----------------------------
    async def connect(
        self,
        *,
        telemetry_callback: Callable[[DriveSnapshot], None] | None = None,
    ) -> None:
        """Connect to the drive and start background tasks."""
        if self._session is not None:
            return

        if telemetry_callback is not None:
            self._telemetry_callback = telemetry_callback

        drive_cfg = self._cfg.drive
        c = drive_cfg.connection
        r = drive_cfg.retry
        poll_config = drive_cfg.poll

        retry_policy = r.to_transport_policy()

        tid_gen = TransactionIdGenerator()

        def build_keepalive_adu() -> bytes:
            req = self._sdo.build_read_int(
                index=int(ODIndex.STATUSWORD),
                subindex=0,
                size=2,
                signed=False,
                transaction_id=tid_gen.next(),
            )
            return req.adu

        keepalive = KeepAliveConfig(
            enabled=True,
            interval_s=float(getattr(poll_config, "keepalive_interval_s", 1.0)),
            build_adu=build_keepalive_adu,
            reconnect_on_error=True,
        )

        # Capture event loop for call_soon_threadsafe reconnect signaling
        self._reconnect_loop = asyncio.get_running_loop()

        def on_reconnect_callback() -> None:
            """Sync callback from keepalive thread — schedules safety stop on event loop."""
            loop = self._reconnect_loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._schedule_reconnect_stop)

        session = ModbusSession(
            host=c.host,
            port=c.port,
            connect_timeout_s=float(c.connect_timeout_s),
            io_timeout_s=float(c.request_timeout_s),
            retry_policy=retry_policy,
            keepalive=keepalive,
            on_reconnect=on_reconnect_callback,
            tid_gen=tid_gen,
            logger=getattr(c, "logger", None),
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._modbus_executor, session.connect)
        self._session = session

        # Build OD-access-based helpers
        self._sm = CiA402StateMachine(self, config=self._cfg.state_machine)
        self._pp = ProfilePosition(self, config=self._cfg.profile_position,
                                   abort_event=self._abort_event)
        self._pv = ProfileVelocity(self, config=self._cfg.profile_velocity,
                                   abort_event=self._abort_event)
        from ..motion.homing import Homing
        self._homing = Homing(self, config=self._cfg.homing,
                             abort_event=self._abort_event)
        self._jog = JogController(self, config=self._cfg.jog,
                                  abort_event=self._abort_event)

        # Start telemetry poller for state cache
        telemetry_cfg = TelemetryConfig(
            interval_s=float(getattr(poll_config, "telemetry_poll_s", 0.5)),
            read_position=True,
            read_velocity=True,
            read_mode_display=True,
            tolerate_errors=True,
        )
        self._telemetry_poller = TelemetryPoller(self, config=telemetry_cfg, on_snapshot=self._telemetry_callback)
        self._telemetry_poller.start()

        # Set software position limits
        limits = self._cfg.drive.limits
        if limits.min_position_limit is not None and limits.max_position_limit is not None:
            try:
                await self.set_position_limits(limits.min_position_limit, limits.max_position_limit)
                _LOGGER.info(
                    "Software position limits set: %s - %s (drive units)",
                    limits.min_position_limit, limits.max_position_limit,
                )
            except Exception as e:
                _LOGGER.warning("Failed to set software position limits: %s", e)

        await self._validate_connection()

    async def close(self) -> None:
        """Close connection and release resources.  Idempotent."""
        if self._session is None:
            self._sm = self._pp = self._pv = self._homing = self._jog = None
            return

        self._cancel_idle_shutdown_timer()

        if self._jog is not None:
            try:
                await self._jog.close()
            except Exception:
                pass

        if self._telemetry_poller is not None:
            try:
                await self._telemetry_poller.stop()
            except Exception:
                pass
            self._telemetry_poller = None

        session = self._session
        self._session = None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._modbus_executor, session.close)
        self._modbus_executor.shutdown(wait=True, cancel_futures=True)

        self._reconnect_loop = None
        self._sm = self._pp = self._pv = self._homing = self._jog = None

    async def _validate_connection(self) -> None:
        """Post-connect validation: verify communication and position limit sanity."""
        try:
            sw = await self.read_u16(int(ODIndex.STATUSWORD))
            decoded = decode_statusword(sw)
            state = infer_cia402_state(sw)
            _LOGGER.info("Post-connect validation: statusword=0x%04X, state=%s", sw, state.name)

            if decoded.get("fault"):
                _LOGGER.warning(
                    "Drive is in FAULT state at startup (statusword=0x%04X). "
                    "Consider calling fault_reset() before operation.", sw,
                )
        except Exception as e:
            _LOGGER.warning("Post-connect validation: failed to read statusword: %s", e)
            return

        try:
            min_pos, max_pos = await self.get_position_limits()
            _LOGGER.info("Post-connect validation: position limits min=%s, max=%s", min_pos, max_pos)

            if min_pos >= max_pos:
                # Some devices return 0/0 for position limit registers (e.g.
                # when the gateway exposes them as 16-bit objects that
                # zero-pad).  This is not a fatal misconfiguration — the
                # software limits from DryveD1Config still guard motion.
                _LOGGER.warning(
                    "Position limit registers report min=%d >= max=%d "
                    "(0x607B / 0x607D).  The device may not support these "
                    "registers or returns them as 16-bit values.  Software "
                    "position limits from config will be used instead.",
                    min_pos, max_pos,
                )
        except Exception as e:
            _LOGGER.warning("Post-connect validation: failed to read position limits: %s", e)

        try:
            homed = await self.is_homed()
            _LOGGER.info("Post-connect validation: homed=%s", homed)
        except Exception as e:
            _LOGGER.debug("Post-connect validation: failed to read homing status: %s", e)

    # -----------------------------
    # Connection status
    # -----------------------------
    @property
    def is_connected(self) -> bool:
        """Check if connected, using freshness-based check if telemetry poller is active."""
        if self._session is None:
            return False

        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None:
                poll_config = self._cfg.drive.poll
                keepalive_interval = float(poll_config.keepalive_interval_s)
                miss_limit = int(poll_config.keepalive_miss_limit)
                max_age = miss_limit * keepalive_interval
                age = monotonic_s() - snapshot.ts_monotonic_s
                if age < max_age:
                    return True

        return self._session.is_connected

    # -----------------------------
    # Telemetry (public integration API)
    # -----------------------------
    def set_telemetry_callback(self, cb: Callable[[DriveSnapshot], None] | None) -> None:
        """Attach a snapshot callback to the internal telemetry poller."""
        self._telemetry_callback = cb
        if self._telemetry_poller is not None:
            self._telemetry_poller.set_callback(cb)

    def telemetry_latest(self) -> DriveSnapshot | None:
        """Return the latest cached telemetry snapshot, if available."""
        if self._telemetry_poller is None:
            return None
        return self._telemetry_poller.latest

    def telemetry_poll_info(self) -> dict[str, Any]:
        """Return basic poller info for diagnostics."""
        if self._telemetry_poller is None:
            return {"is_running": False, "interval_s": None}
        return {
            "is_running": bool(self._telemetry_poller.is_running),
            "interval_s": float(self._telemetry_poller.interval_s),
        }

    # -----------------------------
    # Reconnect safety
    # -----------------------------
    def _schedule_reconnect_stop(self) -> None:
        """Callback scheduled via call_soon_threadsafe from the keepalive thread."""
        if self._reconnect_stop_scheduled:
            _LOGGER.debug("reconnect stop already scheduled, skipping duplicate")
            return
        self._reconnect_stop_scheduled = True

        task = asyncio.get_running_loop().create_task(
            self._stop_motion_on_reconnect(),
            name="reconnect-safety-stop",
        )

        def _on_done(t: asyncio.Task) -> None:
            self._reconnect_stop_scheduled = False
            if not t.cancelled() and t.exception() is not None:
                _LOGGER.error("Reconnect safety stop failed: %s", t.exception())

        task.add_done_callback(_on_done)

    async def _stop_motion_on_reconnect(self) -> None:
        """Stop active motion after reconnect (safety: fail-closed on reconnect).

        Fire-and-forget safety handler with retry — failures are logged at
        ERROR level.
        """
        if self._jog is not None and self._jog.state.active:
            action, name = self._jog.release, "jog release"
        else:
            action, name = self.stop, "stop"

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await action()
                return
            except Exception:
                if attempt == max_attempts:
                    _LOGGER.error(
                        "SAFETY: %s failed after %d attempts — motor may still be moving",
                        name,
                        max_attempts,
                        exc_info=True,
                    )
                else:
                    _LOGGER.warning(
                        "SAFETY: %s attempt %d/%d failed, retrying",
                        name,
                        attempt,
                        max_attempts,
                        exc_info=True,
                    )
                    await asyncio.sleep(0.1)
