from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_LOGGER_MODBUS = logging.getLogger("dryve_d1.modbus")
_LOGGER = logging.getLogger(__name__)

from ..cia402.state_machine import CiA402StateMachine, StateMachineConfig
from ..config.models import DriveConfig as UserDriveConfig
from ..motion.homing import Homing, HomingConfig, HomingResult
from ..motion.jog import JogConfig as MotionJogConfig
from ..motion.jog import JogController
from ..motion.profile_position import ProfilePosition, ProfilePositionConfig
from ..motion.profile_velocity import ProfileVelocity, ProfileVelocityConfig
from ..od.controlword import cw_disable_voltage
from ..od.indices import ODIndex
from ..od.statusword import decode_statusword
from ..protocol import SDOClient
from ..telemetry.poller import TelemetryConfig, TelemetryPoller
from ..telemetry.snapshots import DriveSnapshot
from ..transport import ModbusSession
from ..transport.clock import monotonic_s
from ..transport.retry import RetryPolicy as TransportRetryPolicy
from ..transport.session import KeepAliveConfig


@dataclass(frozen=True, slots=True)
class DryveD1Config:
    """High-level configuration for the DryveD1 facade.

    `drive` comes from `dryve_d1.config.models.DriveConfig` (Pydantic model or dataclass fallback),
    and contains connection, retry, polling and soft-limit settings.
    """

    drive: UserDriveConfig
    state_machine: StateMachineConfig = StateMachineConfig()
    profile_position: ProfilePositionConfig = ProfilePositionConfig()
    profile_velocity: ProfileVelocityConfig = ProfileVelocityConfig()
    homing: HomingConfig = HomingConfig()
    jog: MotionJogConfig = MotionJogConfig()


class DryveD1:
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
        """Initialize DryveD1 driver.
        
        According to contract:
        - Preconditions: config != None, config.drive.connection.host non-empty,
          config.drive.connection.port ∈ [1, 65535], config.drive.connection.unit_id ∈ [0, 255],
          all timeout values > 0
        - Postconditions: _session == None, _sm == None, _pp == None, _pv == None,
          _homing == None, _jog == None, is_connected == False
        """
        if config is None:
            raise ValueError("config must not be None")
        
        self._cfg = config

        c = self._cfg.drive.connection
        
        # Validate connection parameters according to contract
        if not c.host or len(c.host.strip()) == 0:
            raise ValueError("config.drive.connection.host must be non-empty")
        if not (1 <= c.port <= 65535):
            raise ValueError(f"config.drive.connection.port must be in range [1, 65535], got {c.port}")
        if not (0 <= c.unit_id <= 255):
            raise ValueError(f"config.drive.connection.unit_id must be in range [0, 255], got {c.unit_id}")
        
        # Validate timeout values (must be > 0)
        if c.connect_timeout_s <= 0:
            raise ValueError(f"config.drive.connection.connect_timeout_s must be > 0, got {c.connect_timeout_s}")
        if c.request_timeout_s <= 0:
            raise ValueError(f"config.drive.connection.request_timeout_s must be > 0, got {c.request_timeout_s}")
        if c.socket_idle_timeout_s <= 0:
            raise ValueError(f"config.drive.connection.socket_idle_timeout_s must be > 0, got {c.socket_idle_timeout_s}")
        
        self._sdo = SDOClient(unit_id=c.unit_id)

        self._session: ModbusSession | None = None

        # Higher-level helpers (created after connect)
        self._sm: CiA402StateMachine | None = None
        self._pp: ProfilePosition | None = None
        self._pv: ProfileVelocity | None = None
        self._homing: Homing | None = None
        self._jog: JogController | None = None
        
        # B4: Reconnect safety - event to signal reconnect from ModbusSession callback
        # Use threading.Event since callback runs in sync context (ModbusSession uses threads)
        self._reconnect_event: threading.Event | None = None
        
        # Optional TelemetryPoller snapshot callback (set by integrators)
        self._telemetry_callback: Callable[[DriveSnapshot], None] | None = None

        # M1: State cache via TelemetryPoller
        self._telemetry_poller: TelemetryPoller | None = None
        
        # Idle shutdown: after motion stops, schedule disable_voltage after delay.
        # Two-phase design:
        #   Phase 1 (delay):  TimerHandle from loop.call_later()  — cancel is synchronous
        #   Phase 2 (action): Task running _idle_shutdown_action() — cancel via Task.cancel()
        # _cancel_idle_shutdown_timer() kills whichever phase is active.
        self._idle_shutdown_handle: asyncio.TimerHandle | None = None
        self._idle_shutdown_task: asyncio.Task[None] | None = None
        self._idle_shutdown_delay_s: float = 8.0  # seconds

        # Abort event: set by stop()/quick_stop() to immediately break
        # any running wait_target_reached() polling loop.
        self._abort_event = asyncio.Event()

    # -----------------------------
    # Lifecycle
    # -----------------------------
    async def connect(self) -> None:
        if self._session is not None:
            return

        drive_cfg = self._cfg.drive
        c = drive_cfg.connection
        r = drive_cfg.retry
        poll_config = drive_cfg.poll

        # Map config.models.RetryPolicy -> transport.retry.RetryPolicy
        retry_max_attempts = getattr(r, "max_attempts", None)
        max_attempts = int(retry_max_attempts) if retry_max_attempts is not None else 3
        base_delay_s = float(getattr(r, "base_delay_s", 0.25))
        max_delay_s = float(getattr(r, "max_delay_s", 5.0))
        jitter_s = float(getattr(r, "jitter_s", 0.10))
        jitter_fraction = min(0.90, jitter_s / max(base_delay_s, 1e-6))

        retry_policy = TransportRetryPolicy(
            max_attempts=max_attempts,
            base_delay_s=base_delay_s,
            backoff_factor=2.0,
            max_delay_s=max_delay_s,
            jitter_fraction=jitter_fraction,
        )

        # Keepalive: build a minimal Statusword read (0x6041).
        # The manual indicates the port may close if heartbeats are missing; this avoids idling.
        session: ModbusSession

        # We must create session first to access transaction-id generator inside keepalive builder.
        # Keepalive builder runs from a session-owned thread, so it must be thread-safe and lock-free.
        session = ModbusSession(
            host=c.host,
            port=c.port,
            connect_timeout_s=float(c.connect_timeout_s),
            io_timeout_s=float(c.request_timeout_s),
            retry_policy=retry_policy,
            keepalive=None,  # set below
            logger=getattr(c, "logger", None),
        )

        def build_keepalive_adu() -> bytes:
            tid = session.next_transaction_id()
            req = self._sdo.build_read_int(
                index=int(ODIndex.STATUSWORD),
                subindex=0,
                size=2,
                signed=False,
                transaction_id=tid,
            )
            return req.adu

        keepalive = KeepAliveConfig(
            enabled=True,
            interval_s=float(getattr(poll_config, "keepalive_interval_s", 1.0)),
            build_adu=build_keepalive_adu,
            reconnect_on_error=True,
        )

        # B4: Create reconnect event for safety (threading.Event since callback is sync)
        self._reconnect_event = threading.Event()
        
        # B4: Create reconnect callback for safety
        def on_reconnect_callback() -> None:
            """Synchronous callback called when reconnect happens (B4 requirement)."""
            # Set event to signal reconnect to async code
            if self._reconnect_event is not None:
                self._reconnect_event.set()
        
        # Recreate session with keepalive enabled (cleaner than mutating internals).
        session.close()
        session = ModbusSession(
            host=c.host,
            port=c.port,
            connect_timeout_s=float(c.connect_timeout_s),
            io_timeout_s=float(c.request_timeout_s),
            retry_policy=retry_policy,
            keepalive=keepalive,
            on_reconnect=on_reconnect_callback,  # B4: Reconnect safety callback
            logger=getattr(c, "logger", None),
        )

        await asyncio.to_thread(session.connect)
        self._session = session

        # Build OD-access-based helpers
        self._sm = CiA402StateMachine(self, config=self._cfg.state_machine)
        self._pp = ProfilePosition(self, config=self._cfg.profile_position,
                                   abort_event=self._abort_event)
        self._pv = ProfileVelocity(self, config=self._cfg.profile_velocity)
        self._homing = Homing(self, config=self._cfg.homing,
                             abort_event=self._abort_event)
        self._jog = JogController(self, config=self._cfg.jog)
        
        # M1: Start telemetry poller for state cache
        telemetry_cfg = TelemetryConfig(
            interval_s=float(getattr(poll_config, "telemetry_poll_s", 0.5)),
            read_position=True,
            read_velocity=True,
            read_mode_display=True,
            tolerate_errors=True,
        )
        self._telemetry_poller = TelemetryPoller(self, config=telemetry_cfg, on_snapshot=self._telemetry_callback)
        self._telemetry_poller.start()
        
        # Set software position limits (default: 0-120000 drive units)
        limits = self._cfg.drive.limits
        if limits.min_position_limit is not None and limits.max_position_limit is not None:
            try:
                await self.set_position_limits(limits.min_position_limit, limits.max_position_limit)
                import logging
                logger = logging.getLogger(__name__)
                logger.info(
                    f"Software position limits set: {limits.min_position_limit} - {limits.max_position_limit} "
                    f"(drive units)"
                )
            except Exception as e:
                # Log warning but don't fail connection if limits can't be set
                # (some drives may not support software limits)
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to set software position limits: {e}")

        # Post-connect validation: verify communication and position limits
        await self._validate_connection()

    async def close(self) -> None:
        """Close connection and clean up resources.
        
        According to contract:
        - Must check for active motion operations and raise RuntimeError if present
        - Must stop jog if active
        - Must clear all components (_sm, _pp, _pv, _homing, _jog)
        - Must close TCP connection
        - Must complete within 2.0s
        - Must be idempotent (safe to call multiple times)
        """
        if self._session is None:
            # Already closed, but ensure all components are None
            self._sm = None
            self._pp = None
            self._pv = None
            self._homing = None
            self._jog = None
            return
        
        # Check for active motion operations
        try:
            if self.is_connected:
                # Check if motion is active — attempt to stop it gracefully
                is_moving = await self.is_motion()
                if is_moving:
                    # Try to stop motion before closing (best effort)
                    try:
                        await self.stop()
                    except Exception:
                        pass
                    # Wait briefly for motion to cease
                    await asyncio.sleep(0.2)
                
                # Check if jog is active
                if self._jog is not None:
                    jog_state = self._jog.state
                    if jog_state.active:
                        # Stop jog before closing
                        try:
                            await self._jog.release()
                        except Exception:
                            pass  # Best effort to stop jog
        except Exception:
            # If we can't check status (e.g., connection lost), proceed with cleanup
            pass
        
        # Cancel idle shutdown timer if active
        self._cancel_idle_shutdown_timer()
        
        # Stop jog controller if active (cleanup watchdog task)
        if self._jog is not None:
            try:
                await self._jog.close()
            except Exception:
                pass  # Best effort cleanup
        
        # M1: Stop telemetry poller
        if self._telemetry_poller is not None:
            try:
                await self._telemetry_poller.stop()
            except Exception:
                pass  # Best effort cleanup
            self._telemetry_poller = None
        
        # Close session
        session = self._session
        self._session = None
        await asyncio.to_thread(session.close)
        
        # B4: Clear reconnect event
        self._reconnect_event = None
        
        # Clear all components
        self._sm = None
        self._pp = None
        self._pv = None
        self._homing = None
        self._jog = None
        self._telemetry_poller = None

    async def _validate_connection(self) -> None:
        """Post-connect validation: verify communication and position limit sanity.

        This is a best-effort health check after connect(). Failures are logged
        as warnings — they don't prevent operation, but call attention to
        potential configuration or wiring issues.

        Checks performed:
        1. Read statusword — verifies basic Modbus communication works
        2. Read CiA402 state — logs current state (e.g., FAULT on startup)
        3. Read position limits back — detects swapped MIN/MAX registers
        4. Read homing status — logs whether drive is homed
        """
        import logging
        logger = logging.getLogger(__name__)

        # 1. Verify communication by reading statusword
        try:
            sw = await self.read_u16(int(ODIndex.STATUSWORD))
            decoded = decode_statusword(sw)
            from ..od.statusword import infer_cia402_state
            state = infer_cia402_state(sw)
            logger.info(f"Post-connect validation: statusword=0x{sw:04X}, state={state.name}")

            if decoded.get("fault"):
                logger.warning(
                    f"Drive is in FAULT state at startup (statusword=0x{sw:04X}). "
                    "Consider calling fault_reset() before operation."
                )
        except Exception as e:
            logger.warning(f"Post-connect validation: failed to read statusword: {e}")
            return  # If we can't even read statusword, skip remaining checks

        # 2. Read position limits back and verify sanity
        try:
            min_pos, max_pos = await self.get_position_limits()
            logger.info(f"Post-connect validation: position limits min={min_pos}, max={max_pos}")

            if min_pos >= max_pos:
                logger.critical(
                    f"POSITION LIMIT SANITY CHECK FAILED: min_position ({min_pos}) >= max_position ({max_pos}). "
                    f"This usually means registers 0x607B/0x607D are swapped in the driver. "
                    f"The drive will clamp all positions to {max_pos}, effectively preventing movement. "
                    f"Check ODIndex.MIN_POSITION_LIMIT and ODIndex.MAX_POSITION_LIMIT mapping."
                )
        except Exception as e:
            logger.warning(f"Post-connect validation: failed to read position limits: {e}")

        # 3. Read homing status
        try:
            homed = await self.is_homed()
            logger.info(f"Post-connect validation: homed={homed}")
        except Exception as e:
            logger.debug(f"Post-connect validation: failed to read homing status: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if connected, using freshness-based check if telemetry poller is active (M1).
        
        Priority:
        1. If telemetry snapshot is fresh → True (fast path, no I/O)
        2. If telemetry snapshot is stale → fall through to session check
           (poller may lag during high load, but TCP session is still alive)
        3. Fallback to session-level TCP check (ground truth)
        """
        if self._session is None:
            return False
        
        # M1: Use freshness-based connection check if telemetry poller is active
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None:
                # Check freshness: now - last_ok < miss_limit * interval
                poll_config = self._cfg.drive.poll
                keepalive_interval = float(getattr(poll_config, "keepalive_interval_s", 1.0))
                miss_limit = int(getattr(poll_config, "keepalive_miss_limit", 3))
                max_age = miss_limit * keepalive_interval
                age = monotonic_s() - snapshot.ts_monotonic_s
                if age < max_age:
                    return True
                # Snapshot too old — fall through to session check
                # (poller may lag under load, but TCP session may still be alive)
            # If snapshot is None (poller not yet started or no data yet), fall back to session check
            # This is important right after connect() when poller hasn't created first snapshot yet
        
        # Fallback to session-level check
        return self._session.is_connected

    # -----------------------------
    # Telemetry (public integration API)
    # -----------------------------
    def set_telemetry_callback(self, cb: Callable[[DriveSnapshot], None] | None) -> None:
        """Attach a snapshot callback to the internal telemetry poller.

        The callback is executed in the poller's asyncio task context, so it MUST be fast.
        Use `asyncio.create_task(...)` for any I/O work.
        """
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
        cfg = getattr(self._telemetry_poller, "_cfg", None)
        interval = float(getattr(cfg, "interval_s", 0.0)) if cfg is not None else None
        return {"is_running": bool(self._telemetry_poller.is_running), "interval_s": interval}



    # -----------------------------
    # Low-level OD access (AsyncODAccessor)
    # -----------------------------
    async def _transceive(self, adu: bytes) -> bytes:
        if self._session is None:
            raise RuntimeError("Not connected")
        
        # B4: Reconnect safety - check reconnect event set by ModbusSession callback
        if self._reconnect_event is not None and self._reconnect_event.is_set():
            self._reconnect_event.clear()
            await self._stop_motion_on_reconnect()
        
        deadline = monotonic_s() + float(self._cfg.drive.connection.request_timeout_s)
        return await asyncio.to_thread(self._session.transceive, adu, deadline_s=deadline)

    async def _stop_motion_on_reconnect(self) -> None:
        """Stop active motion after reconnect (safety requirement B4).
        
        Per SRS safety requirements: if connection was lost during active PV/jog motion,
        the drive may continue moving. After reconnect, we must immediately stop motion.
        """
        try:
            # Stop jog if active
            if self._jog is not None:
                jog_state = self._jog.state
                if jog_state.active:
                    try:
                        await self._jog.release()
                    except Exception:
                        # Best effort - if stop fails, continue
                        pass
                else:
                    # If jog is not active, try general stop
                    try:
                        await self.stop()
                    except Exception:
                        # Best effort
                        pass
            else:
                # No jog controller, try general stop
                try:
                    await self.stop()
                except Exception:
                    # Best effort
                    pass
        except Exception:
            # Best effort - don't let reconnect safety block reconnection
            pass

    def _next_tid(self) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")
        return self._session.next_transaction_id()

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=2, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = self._sdo.decode_read_int(resp, request=req, signed=False) & 0xFFFF
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=2 u16 -> %d (0x%04X)", index, subindex, value, value)
        return value

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=4, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=True))
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=4 i32 -> %d", index, subindex, value)
        return value

    async def read_u32(self, index: int, subindex: int = 0) -> int:
        """Read UINT32 value from Object Dictionary.
        
        Args:
            index: Object Dictionary index
            subindex: Object Dictionary subindex (default 0)
            
        Returns:
            Unsigned 32-bit integer value
        """
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=4, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=False))
        value = value & 0xFFFFFFFF
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=4 u32 -> %d", index, subindex, value)
        return value

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        tid = self._next_tid()
        req = self._sdo.build_read_int(index=index, subindex=subindex, size=1, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        value = int(self._sdo.decode_read_int(resp, request=req, signed=True))
        _LOGGER_MODBUS.debug("READ  index=0x%04X sub=%d size=1 i8  -> %d", index, subindex, value)
        return value

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=2 u16 value=%d (0x%04X)", index, subindex, value, value & 0xFFFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=2, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=4 u32 value=%d", index, subindex, value & 0xFFFFFFFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=4, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=4 i32 value=%d", index, subindex, value)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=4, signed=True, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        tid = self._next_tid()
        _LOGGER_MODBUS.debug("WRITE index=0x%04X sub=%d size=1 u8  value=%d (0x%02X)", index, subindex, value, value & 0xFF)
        req = self._sdo.build_write_int(index=index, subindex=subindex, value=value, size=1, signed=False, transaction_id=tid)
        resp = await self._transceive(req.adu)
        self._sdo.parse_write_response(resp, request=req)

    # -----------------------------
    # High-level controls
    # -----------------------------
    async def enable_operation(self) -> None:
        """Enable operation (bring drive to OPERATION_ENABLED state).
        
        According to contract, this method should use run_to_operation_enabled()
        to handle all valid state transitions from any valid starting state.
        """
        sm = self._require_sm()
        await sm.run_to_operation_enabled()

    async def disable_voltage(self) -> None:
        await self.write_u16(int(ODIndex.CONTROLWORD), cw_disable_voltage(), 0)

    async def quick_stop(self, *, op_id: str | None = None) -> None:
        """Request quick stop.
        
        This method NEVER raises — stop must be bulletproof.
        The abort event is the reliable in-process mechanism; all socket I/O
        (HALT write, CiA402 transition) is best-effort only.
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        # Signal abort FIRST — this breaks any running wait_target_reached() immediately
        self._abort_event.set()
        _LOGGER.info("quick_stop[%s]: abort_event set", op_id)

        # Write HALT bit immediately as belt-and-suspenders physical stop
        await self._halt_motor()
        _LOGGER.debug("quick_stop[%s]: HALT command sent", op_id)

        # Best-effort CiA402 quick stop transition
        try:
            status = await self.get_status()
            if not (status.get("operation_enabled", False) or status.get("quick_stop", False)):
                _LOGGER.info("quick_stop[%s]: skipped (drive not enabled/quick_stop inactive)", op_id)
                return
            sm = self._require_sm()
            await sm.quick_stop()
            _LOGGER.info("quick_stop[%s]: state-machine transition requested", op_id)
        except Exception:
            # Socket errors during stop are non-fatal — abort event already set
            _LOGGER.debug("quick_stop[%s]: best-effort path failed", op_id, exc_info=True)
            pass

    async def stop(self, *, op_id: str | None = None) -> None:
        """Stop movement using normal deceleration.
        
        This method NEVER raises — stop must be bulletproof.
        The abort event is the reliable in-process mechanism; all socket I/O
        (HALT write, mode detection, CiA402 transition) is best-effort only.
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        # Signal abort FIRST — this breaks any running wait_target_reached() immediately
        self._abort_event.set()
        _LOGGER.info("stop[%s]: abort_event set", op_id)

        # Write HALT bit immediately as belt-and-suspenders physical stop
        await self._halt_motor()
        _LOGGER.debug("stop[%s]: HALT command sent", op_id)

        # Best-effort mode-specific stop
        try:
            status = await self.get_status()
            if not (status.get("operation_enabled", False) or status.get("quick_stop", False)):
                _LOGGER.info("stop[%s]: skipped (drive not enabled/quick_stop inactive)", op_id)
                return
            
            sm = self._require_sm()
            
            mode_display = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            current_mode = int(mode_display)
            _LOGGER.info("stop[%s]: mode-aware stop path (mode_display=%s)", op_id, current_mode)
            
            if current_mode == 1:  # Profile Position mode
                if self._pp is not None:
                    await self._pp.stop()
            elif current_mode == 3:  # Profile Velocity mode
                if self._pv is not None:
                    await self._pv.stop()
            else:
                await sm.quick_stop()
        except Exception:
            # Socket errors during stop are non-fatal — abort event + HALT already applied
            _LOGGER.debug("stop[%s]: best-effort path failed", op_id, exc_info=True)
            pass

    async def emergency_shutdown(self) -> None:
        """Emergency shutdown (alias for disable_voltage).
        
        This is equivalent to disable_voltage() and provided for API compatibility
        with the old driver version.
        
        **Important:** This is NOT a safety-rated emergency stop. This is a logical
        CiA402 shutdown operation that disables voltage via controlword (0x6040).
        For safety-critical applications, use hardware emergency stop circuits.
        
        Per CiA402 standard, this command moves the drive to "Switch on disabled"
        state by clearing all controlword bits (sending 0x0000).
        """
        await self.disable_voltage()

    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None:
        """Reset fault and optionally perform full recovery to Operation Enabled.
        
        Per SRS requirement M5: after fault during motion, full recovery procedure should:
        - Reset fault (pulse bit 7)
        - Transition state machine to Operation Enabled
        - Set HALT=1 as safe base (motion layers will clear HALT when starting movement)
        
        Args:
            recover: If True (default), perform full recovery to Operation Enabled with HALT=1.
                    If False, only reset fault (legacy behavior).
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        _LOGGER.info("fault_reset[%s]: requested recover=%s", op_id, recover)
        sm = self._require_sm()
        
        # Check if we're in fault before reset
        status_before = await self.get_status()
        was_in_fault = status_before.get("fault", False)
        # Perform fault reset
        await sm.fault_reset()
        # M5: Full recovery procedure if requested
        if recover and was_in_fault:
            # Transition to Operation Enabled
            await sm.run_to_operation_enabled()
            # Set HALT=1 as safe base (per manual: after fault during movement, HALT must be set)
            # This prevents accidental movement restart until explicitly cleared
            from ..od.controlword import CWBit, cw_enable_operation, cw_set_bits
            halt_word = cw_set_bits(cw_enable_operation(), CWBit.HALT)
            await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_word) & 0xFFFF, 0)
        _LOGGER.info("fault_reset[%s]: completed recover=%s was_in_fault=%s", op_id, recover, was_in_fault)
    async def is_homed(self) -> bool:
        """Check if homing has been completed.
        
        Reads the homing status register (0x2014) from the drive.
        Returns True if homing is complete, False otherwise.
        
        Note: This is a dryve D1 specific register. Some drives may not
        support this register, in which case this method may raise an error.
        """
        try:
            homing_status = await self.read_u16(int(ODIndex.HOMING_STATUS), 0)
            # Homing status is typically 1 when homed, 0 when not homed
            return bool(homing_status & 0x01)
        except Exception:
            # If register doesn't exist or read fails, assume not homed
            # This allows the drive to work even if homing status is not available
            return False

    async def move_to_position(
        self,
        *,
        target_position: int,
        velocity: int,
        accel: int,
        decel: int,
        timeout_s: float = 20.0,
        require_homing: bool = True,
        op_id: str | None = None,
    ) -> None:
        """Move to target position.
        
        According to contract:
        - Preconditions: OPERATION_ENABLED, valid parameters within limits
        - Postconditions: Mode = 1, target position set, movement started
        
        Args:
            target_position: Target position in drive units
            velocity: Profile velocity (must be > 0)
            accel: Profile acceleration (must be > 0)
            decel: Profile deceleration (must be > 0)
            timeout_s: Timeout for the move operation (must be > 0)
            require_homing: If True, check that homing is done before moving.
                          If False, skip homing check (use with caution).
        
        Raises:
            RuntimeError: If not connected or not in OPERATION_ENABLED
            ValueError: If parameters are outside valid ranges
            RuntimeWarning: If require_homing=True and not homed
        """
        op_id = op_id or uuid.uuid4().hex[:8]

        # Precondition: must be connected
        if not self.is_connected:
            raise RuntimeError("Not connected")

        # Cancel idle shutdown (both phases: pending delay + running action)
        self._cancel_idle_shutdown_timer()
        
        # Check status (live read; cached telemetry may lag after quick-stop transitions)
        status = await self.get_status_live()
        _LOGGER.info(
            "move_to_position[%s]: start target=%s vel=%s acc=%s dec=%s timeout_s=%.2f status=%s",
            op_id,
            target_position,
            velocity,
            accel,
            decel,
            float(timeout_s),
            {k: status.get(k) for k in ("operation_enabled", "remote", "fault", "target_reached")},
        )
        
        # Precondition: must not be in FAULT (explicit fault reset required)
        if status.get("fault", False):
            raise RuntimeError("Drive is in FAULT state. Call fault_reset() first before move_to_position()")
        
        # Stop any active jog first (belt-and-suspenders; frontend should have stopped it)
        try:
            jog = self._require_jog()
            if jog.state.active:
                await jog.release()
                await asyncio.sleep(0.1)  # let motor decelerate
                _LOGGER.info("move_to_position[%s]: active jog released before PP move", op_id)
        except Exception:
            _LOGGER.debug("move_to_position[%s]: jog pre-stop skipped/failed", op_id, exc_info=True)
            pass
        
        # Ensure drive is in OPERATION_ENABLED with PP mode active.
        #
        # The dryve D1 does NOT reliably activate the PP motion controller when
        # the mode register is written in OPERATION_ENABLED state.  Symptoms:
        # setpoint accepted (target_reached clears) but motor doesn't physically
        # move.  CiA 402 recommends mode changes in ≤ SWITCHED_ON state.
        #
        # Strategy:
        #   1. If NOT enabled: write mode=PP early, THEN enable.
        #   2. If already enabled but mode≠PP: cycle SM down to READY_TO_SWITCH_ON,
        #      write mode=PP, re-enable.
        #   3. If already enabled in PP: no-op (fast path for consecutive PP moves).
        MODE_PP = 1
        need_enable = not status.get("operation_enabled", False)
        wrong_mode = False

        if not need_enable:
            # Already enabled — check current mode
            try:
                current_mode = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
                wrong_mode = (current_mode != MODE_PP)
            except Exception:
                wrong_mode = True  # can't read → treat as unknown mode

        if need_enable or wrong_mode:
            sm = self._require_sm()
            if wrong_mode and not need_enable:
                # Cycle down from OPERATION_ENABLED so mode switch is clean
                _LOGGER.info(
                    "move_to_position[%s]: mode≠PP, cycling SM for clean transition", op_id,
                )
                await sm.shutdown()  # → READY_TO_SWITCH_ON

            # Write mode *before* enable — CiA 402 spec: mode changes in ≤ SWITCHED_ON
            await self.write_u8(int(ODIndex.MODES_OF_OPERATION), MODE_PP, 0)
            await asyncio.sleep(0.05)  # let the drive latch the mode
            _LOGGER.debug("move_to_position[%s]: mode=PP written before enable", op_id)

            await self.enable_operation()
            status = await self.get_status_live()
            _LOGGER.info("move_to_position[%s]: enable_operation completed", op_id)
        
        # Precondition: REMOTE (bit 9) must be enabled (required for dryve D1)
        if not status.get("remote", False):
            from ..cia402.dominance import PreconditionFailed
            raise PreconditionFailed(
                "Remote not enabled: Statusword bit 9 is LOW (DI7 'Enable' must be HIGH). "
                f"Required for motion operations. statusword=0x{await self.read_u16(int(ODIndex.STATUSWORD)):04X}"
            )
        
        # Parameter validation according to contract
        limits = self._cfg.drive.limits
        
        # Validate timeout
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
        
        # Validate velocity
        if velocity == 0:
            raise ValueError("velocity must be != 0")
        if limits.max_abs_velocity is not None:
            if abs(velocity) > limits.max_abs_velocity:
                raise ValueError(f"velocity {abs(velocity)} exceeds max_abs_velocity {limits.max_abs_velocity}")
        
        # Validate acceleration
        if accel <= 0:
            raise ValueError(f"accel must be > 0, got {accel}")
        if limits.max_abs_accel is not None:
            if accel > limits.max_abs_accel:
                raise ValueError(f"accel {accel} exceeds max_abs_accel {limits.max_abs_accel}")
        
        # Validate deceleration
        if decel <= 0:
            raise ValueError(f"decel must be > 0, got {decel}")
        if limits.max_abs_decel is not None:
            if decel > limits.max_abs_decel:
                raise ValueError(f"decel {decel} exceeds max_abs_decel {limits.max_abs_decel}")
        
        # Hard position limits: clamp to [0, 120000] range
        # Values outside the range are clamped to the boundaries
        MIN_POSITION = 0
        MAX_POSITION = 120000
        
        if target_position < MIN_POSITION:
            target_position = MIN_POSITION
        elif target_position > MAX_POSITION:
            target_position = MAX_POSITION

        # Clear HALT bit in controlword before starting PP move.
        # stop()/quick_stop() may leave HALT set as safety action;
        # with HALT active, Profile Position commands can be accepted but not executed.
        from ..od.controlword import CWBit, cw_clear_bits, cw_enable_operation
        halt_cleared = cw_clear_bits(cw_enable_operation(), CWBit.HALT)
        await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cleared) & 0xFFFF, 0)
        await asyncio.sleep(0.05)
        _LOGGER.debug("move_to_position[%s]: HALT bit cleared before PP command", op_id)
        
        pp = self._require_pp()
        
        # Clear abort event so a previous stop() doesn't immediately cancel this move
        self._abort_event.clear()
        
        # Check homing status if required (warning, not error per contract)
        if require_homing:
            is_homed = await self.is_homed()
            if not is_homed:
                import warnings
                warnings.warn(
                    "Homing has not been completed. Movement may not work correctly. "
                    "Please perform homing (reference) before moving to position.",
                    RuntimeWarning,
                    stacklevel=2,
                )
        
        try:
            await pp.move_to_position(
                target_position=target_position,
                profile_velocity=velocity,
                profile_accel=accel,
                profile_decel=decel,
                timeout_s=timeout_s,
            )
            _LOGGER.info("move_to_position[%s]: completed target=%s", op_id, target_position)
        finally:
            # Schedule idle shutdown instead of immediate disable_voltage.
            # If another motion command arrives within the delay, the timer
            # is cancelled and the drive stays in OPERATION_ENABLED — saving
            # ~250-500ms of re-enable overhead per consecutive move.
            self._schedule_idle_shutdown()

    async def _halt_motor(self) -> None:
        """Write HALT bit (bit 8) to controlword to physically stop the motor.
        
        This is a low-level safety fallback: it directly writes HALT to the
        hardware regardless of mode or state. Used as a belt-and-suspenders
        complement to mode-specific stop commands.
        """
        try:
            from ..od.controlword import CWBit, cw_enable_operation, cw_set_bits
            halt_cw = cw_set_bits(cw_enable_operation(), CWBit.HALT)
            await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cw) & 0xFFFF, 0)
        except Exception:
            pass  # Best effort — never let this prevent the abort from propagating

    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> HomingResult:
        """Perform homing operation.
        
        According to contract:
        - Preconditions: is_connected == True, state = OPERATION_ENABLED, state != FAULT
        - Preconditions: REMOTE (bit 9) must be enabled (required for dryve D1)
        - Postconditions: Mode = 6, homing completed, is_homed() == True
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        _LOGGER.info("home[%s]: requested timeout_s=%.2f", op_id, float(timeout_s))

        # Precondition: must be connected
        if not self.is_connected:
            raise RuntimeError("Not connected")
        
        # Check status first (live read; cached telemetry may lag after quick-stop transitions)
        status = await self.get_status_live()
        
        # Precondition: must not be in FAULT (explicit fault reset required)
        if status.get("fault", False):
            raise RuntimeError("Drive is in FAULT state. Call fault_reset() first before home()")
        
        # Automatically enable operation if not already enabled (but only if not in fault)
        if not status.get("operation_enabled", False):
            await self.enable_operation()
            # Re-check status after enable
            status = await self.get_status_live()
        
        # Precondition: REMOTE (bit 9) must be enabled (required for dryve D1)
        if not status.get("remote", False):
            from ..cia402.dominance import PreconditionFailed
            raise PreconditionFailed(
                "Remote not enabled: Statusword bit 9 is LOW (DI7 'Enable' must be HIGH). "
                f"Required for homing operation. statusword=0x{await self.read_u16(int(ODIndex.STATUSWORD)):04X}"
            )
        
        homing = self._require_homing()
        # Clear abort event so a previous stop() doesn't immediately cancel this homing op
        self._abort_event.clear()
        result = await homing.run(timeout_s=timeout_s)
        _LOGGER.info("home[%s]: completed", op_id)
        return result

    # Jog facade
    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        """Start jogging with given velocity.
        
        According to contract:
        - Preconditions: is_connected == True, state = OPERATION_ENABLED, state != FAULT
        - Preconditions: velocity != 0, abs(velocity) ≤ max_abs_velocity (if limits set)
        - Preconditions: ttl_ms ∈ [50, 5000] (if specified)
        - Postconditions: Mode = 3, target velocity set, motion started
        
        Note: ttl_ms parameter is accepted for API compatibility but the TTL
        is configured in JogConfig when the controller is created.
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        # Cancel idle shutdown (both phases: pending delay + running action)
        self._cancel_idle_shutdown_timer()
        _LOGGER.info("jog_start[%s]: requested velocity=%s ttl_ms=%s", op_id, velocity, ttl_ms)
        
        # Precondition: connect() must have been called (session + helpers exist).
        # We do NOT gate on is_connected here because the session has auto-reconnect
        # in transceive() — a transiently closed socket will be re-opened on first I/O.
        jog = self._require_jog()
        pv = self._require_pv()
        
        # Check status first (live read; cached telemetry may lag after quick-stop transitions)
        status = await self.get_status_live()
        _LOGGER.debug(
            "jog_start[%s]: status=%s",
            op_id,
            {k: status.get(k) for k in ("operation_enabled", "remote", "fault", "target_reached")},
        )
        
        # Precondition: must not be in FAULT (explicit fault reset required)
        if status.get("fault", False):
            raise RuntimeError("Drive is in FAULT state. Call fault_reset() first before jog_start()")
        
        # Automatically enable operation if not already enabled (but only if not in fault)
        if not status.get("operation_enabled", False):
            await self.enable_operation()
            # Re-check status after enable
            status = await self.get_status_live()
        
        # Clear HALT bit in Controlword (e.g., after fault_reset with recover=True)
        # HALT blocks movement in Profile Velocity mode, so we must clear it before jog
        # Note: HALT is a Controlword bit (bit 8), not a Statusword bit
        # After fault_reset with recover=True, HALT may be set, so we explicitly clear it
        from ..od.controlword import CWBit, cw_clear_bits, cw_enable_operation
        halt_cleared = cw_clear_bits(cw_enable_operation(), CWBit.HALT)
        await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cleared) & 0xFFFF, 0)
        # Wait a bit for HALT to clear
        await asyncio.sleep(0.05)
        
        # Precondition: REMOTE (bit 9) must be enabled (required for dryve D1)
        if not status.get("remote", False):
            from ..cia402.dominance import PreconditionFailed
            raise PreconditionFailed(
                "Remote not enabled: Statusword bit 9 is LOW (DI7 'Enable' must be HIGH). "
                f"Required for jog operation. statusword=0x{await self.read_u16(int(ODIndex.STATUSWORD)):04X}"
            )
        
        # Parameter validation according to contract
        limits = self._cfg.drive.limits
        
        # Validate velocity
        if velocity == 0:
            raise ValueError("velocity must be != 0")
        if limits.max_abs_velocity is not None:
            if abs(velocity) > limits.max_abs_velocity:
                raise ValueError(f"velocity {abs(velocity)} exceeds max_abs_velocity {limits.max_abs_velocity}")
        
        # Validate ttl_ms if specified
        if ttl_ms is not None:
            if not (50 <= ttl_ms <= 5000):
                raise ValueError(f"ttl_ms must be in range [50, 5000], got {ttl_ms}")
        
        # Check position limits before starting jog
        # Hard position limits: [0, 120000]
        MIN_POSITION = 0
        MAX_POSITION = 120000
        
        current_position = await self.get_position()
        
        # If moving in positive direction and at/above max, don't start
        if velocity > 0 and current_position >= MAX_POSITION:
            raise RuntimeError(f"Cannot jog: at maximum position {MAX_POSITION} (current: {current_position})")
        
        # If moving in negative direction and at/below min, don't start
        if velocity < 0 and current_position <= MIN_POSITION:
            raise RuntimeError(f"Cannot jog: at minimum position {MIN_POSITION} (current: {current_position})")
        
        # Always start acceleration from 0: stop any existing movement first
        # This ensures smooth acceleration from zero velocity
        if jog.state.active:
            # If jog is already active, stop it first
            await jog.release()
            # Wait a bit for the motor to stop (allow deceleration)
            await asyncio.sleep(0.1)
        else:
            # Even if jog is not active, ensure velocity is 0 before starting
            # This handles cases where motor might still be moving from previous command
            await pv.stop_velocity_zero()
            # Wait a bit to ensure velocity reaches 0
            await asyncio.sleep(0.05)
        
        await jog.press(velocity=velocity)
        _LOGGER.info("jog_start[%s]: command accepted velocity=%s", op_id, velocity)

    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        """Update jog velocity and refresh TTL keepalive.
        
        Note: ttl_ms parameter is accepted for API compatibility but the TTL
        is configured in JogConfig when the controller is created.
        
        Returns silently (200 OK) in all cases:
        - If jog is not active (already stopped / boundary stop / released)
        - If position is at boundary — stops jog and returns
        This prevents HTTP 500 flood from 50ms keepalive interval.
        """
        jog = self._require_jog()
        op_id = op_id or uuid.uuid4().hex[:8]
        
        # Fast path: if jog is already inactive, return immediately (no I/O)
        # This prevents flood of Modbus reads after boundary stop
        if not jog.state.active:
            return
        
        # Check position limits during jog keepalive
        # Hard position limits: [0, 120000]
        MIN_POSITION = 0
        MAX_POSITION = 120000
        
        current_position = await self.get_position()
        
        # If moving in positive direction and at/above max, stop jog silently
        if velocity > 0 and current_position >= MAX_POSITION:
            await self.jog_stop()
            return
        
        # If moving in negative direction and at/below min, stop jog silently
        if velocity < 0 and current_position <= MIN_POSITION:
            await self.jog_stop()
            return
        
        await jog.keepalive(velocity=velocity)

    async def jog_stop(self, *, op_id: str | None = None) -> None:
        """Stop jogging."""
        op_id = op_id or uuid.uuid4().hex[:8]
        jog = self._require_jog()

        # Fast path: if jog is already inactive, skip Modbus I/O and
        # idle-shutdown scheduling.  Prevents redundant work from rapid
        # jog_stop spam (frontend can send 20+ stops per second).
        if not jog.state.active:
            _LOGGER.debug("jog_stop[%s]: already inactive, skipping", op_id)
            return

        await jog.release()
        _LOGGER.info("jog_stop[%s]: release sent", op_id)

        # Schedule idle shutdown (delayed disable_voltage)
        self._schedule_idle_shutdown()
        _LOGGER.debug("jog_stop[%s]: shutdown timer scheduled", op_id)

    # -----------------------------
    # Status and telemetry
    # -----------------------------
    async def get_position(self) -> int:
        """Get current position from the drive.
        
        M1: Uses cached snapshot from TelemetryPoller if available, otherwise reads directly.
        Returns the actual position value (0x6064) as a signed 32-bit integer.
        """
        # M1: Try to use cached snapshot first
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.position is not None:
                return snapshot.position
        
        # Fallback to direct read if cache not available
        return await self.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE))

    async def is_motion(self) -> bool:
        """Check if the drive is currently in motion.
        
        This method is mode-aware and uses appropriate logic for each operation mode:
        - Profile Position (mode=1): Checks target_reached bit and velocity actual
        - Profile Velocity / Jog (mode=3): Checks velocity actual value (abs > threshold)
        - Other modes: Falls back to target_reached bit
        
        Returns True if motion is active, False if the drive is stationary.
        """
        # M1: Try to use cached snapshot first for statusword and mode
        snapshot = None
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
        if snapshot is not None and snapshot.decoded_status is not None:
            decoded = snapshot.decoded_status
            mode_disp = snapshot.mode_display
        else:
            # Fallback to direct reads
            sw = await self.read_u16(int(ODIndex.STATUSWORD))
            decoded = decode_statusword(sw)
            mode_disp = None
        
        # Check jog state first (if jog controller is active, it's authoritative)
        if self._jog is not None:
            jog_state = self._jog.state
            if jog_state.active:
                # Jog is active, check if deadline hasn't expired
                if monotonic_s() < jog_state.deadline_s:
                    return True
                # Deadline expired — TTL watchdog should clear .active soon.
                # Fall through to velocity/target_reached checks below
                # instead of unconditionally returning True.
        
        # Read mode display to determine operation mode (if not from cache)
        if mode_disp is None:
            try:
                mode_disp = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY))
            except Exception:
                # If mode read fails, fall back to target_reached
                return not decoded["target_reached"]
        
        MODE_PROFILE_POSITION = 1
        MODE_PROFILE_VELOCITY = 3
        VELOCITY_THRESHOLD = 10  # Threshold for considering movement (drive units)
        
        if mode_disp == MODE_PROFILE_POSITION:
            # For Profile Position: check target_reached, but also verify with velocity
            if decoded["target_reached"]:
                return False
            # Additional check: verify velocity to avoid false positives
            # M1: Try to use cached velocity first
            vel = None
            if snapshot is not None and snapshot.velocity is not None:
                vel = snapshot.velocity
            else:
                try:
                    vel = await self.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE))
                except Exception:
                    # If velocity read fails, rely on target_reached
                    return not decoded["target_reached"]
            return abs(vel) > VELOCITY_THRESHOLD
        elif mode_disp == MODE_PROFILE_VELOCITY:
            # B3: For Profile Velocity / Jog: use velocity actual value only
            # Do NOT use target_reached bit for PV/Jog (it doesn't reflect motion in this mode)
            # M1: Try to use cached velocity first
            vel = None
            if snapshot is not None and snapshot.velocity is not None:
                vel = snapshot.velocity
            else:
                try:
                    vel = await self.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE))
                except Exception:
                    # If velocity read fails for PV/Jog, check jog state as fallback
                    # (jog state is authoritative if jog controller exists)
                    if self._jog is not None:
                        jog_state = self._jog.state
                        if jog_state.active and monotonic_s() < jog_state.deadline_s:
                            return True
                    # If no jog state or velocity unavailable, conservatively return False
                    # (cannot determine motion state reliably without velocity or jog state)
                    return False
            return abs(vel) > VELOCITY_THRESHOLD
        else:
            # For other modes (e.g., Homing): use target_reached as fallback
            return not decoded["target_reached"]

    async def get_status(self) -> dict[str, bool]:
        """Get decoded statusword from the drive.
        
        M1: Uses cached snapshot from TelemetryPoller if available, otherwise reads directly.
        Returns a dictionary of status flags decoded from the statusword (0x6041).
        """
        # M1: Try to use cached snapshot first
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.decoded_status is not None:
                return snapshot.decoded_status
        
        # Fallback to direct read if cache not available
        sw = await self.read_u16(int(ODIndex.STATUSWORD))
        return decode_statusword(sw)

    async def get_status_live(self) -> dict[str, bool]:
        """Get decoded statusword from a direct OD read (bypass telemetry cache)."""
        sw = await self.read_u16(int(ODIndex.STATUSWORD))
        return decode_statusword(sw)

    # -----------------------------
    # Configuration and limits
    # -----------------------------
    async def set_position_limits(self, min_position: int, max_position: int) -> None:
        """Set software position limits in the drive.
        
        According to CiA 402 standard:
        - 0x607B: Min position limit (INT32)
        - 0x607D: Max position limit (INT32)
        
        These limits are enforced by the drive hardware/firmware and will prevent
        movement beyond the specified range.
        
        Args:
            min_position: Minimum allowed position (in drive units)
            max_position: Maximum allowed position (in drive units)
            
        Raises:
            RuntimeError: If not connected
            ValueError: If min_position >= max_position
        """
        if not self.is_connected:
            raise RuntimeError("Not connected")
        
        if min_position >= max_position:
            raise ValueError(
                f"min_position ({min_position}) must be less than max_position ({max_position})"
            )
        
        # Set min position limit (0x607D)
        await self.write_i32(int(ODIndex.MIN_POSITION_LIMIT), int(min_position), 0)
        
        # Set max position limit (0x607B)
        await self.write_i32(int(ODIndex.MAX_POSITION_LIMIT), int(max_position), 0)
    
    async def get_position_limits(self) -> tuple[int, int]:
        """Get current software position limits from the drive.
        
        Returns:
            Tuple of (min_position, max_position) in drive units
            
        Raises:
            RuntimeError: If not connected
        """
        if not self.is_connected:
            raise RuntimeError("Not connected")
        
        min_pos = await self.read_i32(int(ODIndex.MIN_POSITION_LIMIT), 0)
        max_pos = await self.read_i32(int(ODIndex.MAX_POSITION_LIMIT), 0)
        
        return (min_pos, max_pos)
    
    # -----------------------------
    # Idle shutdown timer management
    # -----------------------------
    def _cancel_idle_shutdown_timer(self) -> None:
        """Cancel idle shutdown in whichever phase it's in.

        Phase 1 (delay pending):  TimerHandle.cancel() is synchronous — the
            callback will simply never fire.
        Phase 2 (action running): Task.cancel() schedules CancelledError
            delivery at the next await inside _idle_shutdown_action().
        """
        if self._idle_shutdown_handle is not None:
            self._idle_shutdown_handle.cancel()
            self._idle_shutdown_handle = None
        if self._idle_shutdown_task is not None:
            self._idle_shutdown_task.cancel()
            self._idle_shutdown_task = None

    def _schedule_idle_shutdown(self) -> None:
        """Schedule disable_voltage after idle delay (two-phase)."""
        self._cancel_idle_shutdown_timer()
        loop = asyncio.get_running_loop()
        self._idle_shutdown_handle = loop.call_later(
            self._idle_shutdown_delay_s, self._fire_idle_shutdown,
        )

    def _fire_idle_shutdown(self) -> None:
        """Timer callback — transition from phase 1 (delay) to phase 2 (action)."""
        self._idle_shutdown_handle = None
        self._idle_shutdown_task = asyncio.ensure_future(
            self._idle_shutdown_action(),
        )

    async def _idle_shutdown_action(self) -> None:
        """Phase 2: perform the actual disable_voltage (cancellable via Task.cancel)."""
        try:
            if self._sm is None:
                return
            from ..cia402.state_machine import CiA402State

            current_state = await self._sm.current_state()
            if current_state in {
                CiA402State.SWITCH_ON_DISABLED,
                CiA402State.READY_TO_SWITCH_ON,
            }:
                return  # already in low-power state
            if self._jog is not None and self._jog.state.active:
                return  # jog still running
            await self.disable_voltage()
            _LOGGER.info(
                "idle_shutdown: disable_voltage after %.0fs idle",
                self._idle_shutdown_delay_s,
            )
        except asyncio.CancelledError:
            pass  # motion command arrived — expected
        except Exception:
            _LOGGER.warning(
                "idle_shutdown: disable_voltage failed (non-fatal)",
                exc_info=True,
            )
        finally:
            self._idle_shutdown_task = None
    
    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _require_sm(self) -> CiA402StateMachine:
        if self._sm is None:
            raise RuntimeError("Not connected")
        return self._sm

    def _require_pp(self) -> ProfilePosition:
        if self._pp is None:
            raise RuntimeError("Not connected")
        return self._pp

    def _require_pv(self) -> ProfileVelocity:
        if self._pv is None:
            raise RuntimeError("Not connected")
        return self._pv

    def _require_homing(self) -> Homing:
        if self._homing is None:
            raise RuntimeError("Not connected")
        return self._homing

    def _require_jog(self) -> JogController:
        if self._jog is None:
            raise RuntimeError("Not connected")
        return self._jog
