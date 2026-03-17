from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

_LOGGER = logging.getLogger(__name__)

from ..od.indices import ODIndex
from ..od.statusword import decode_statusword
from ..protocol.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .profile_velocity import ProfileVelocity, ProfileVelocityConfig

StopMode = Literal["velocity_zero", "halt"]

@dataclass(frozen=True, slots=True)
class JogConfig:
    """Jog controller configuration.

    TTL semantics:
    - User calls `press()` on button down.
    - While button is held, user calls `keepalive()` periodically (e.g., every 50-100ms).
    - If keepalive does not arrive for ttl_s, controller will stop.

    watch_interval_s should be <= ttl_s / 2 for reliable stop-on-loss.
    """

    ttl_s: float = 1.0
    watch_interval_s: float = 0.05
    stop_mode: StopMode = "velocity_zero"

    require_operation_enabled: bool = True  # verify Statusword bits before starting

    # Optional default profile velocity config for jog
    acceleration: int | None = None
    deceleration: int | None = None
    quick_stop_decel: int | None = None
    mode_settle_s: float = 0.3  # Time for drive to switch to PV after writing 0x6060

@dataclass(frozen=True, slots=True)
class JogState:
    active: bool
    velocity: int
    last_update_s: float
    deadline_s: float

class JogController:
    """Hold-to-move jog controller built on Profile Velocity mode.

    This controller is intentionally conservative:
    - It does not perform CiA402 transitions (Enable Operation).
    - It can optionally verify that the drive is in Operation Enabled before moving.
    - It can run a lightweight watchdog asyncio task to stop on TTL expiry.
    """

    def __init__(
        self,
        od: AsyncODAccessor,
        *,
        config: JogConfig | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> None:
        self._od = od
        self._cfg = config or JogConfig()
        self._abort: asyncio.Event | None = abort_event

        # verify_mode=False: gateway/drive often do not report 0x6061==3 in time (TID mismatch / delay).
        # We write 0x6060=3 and wait mode_settle_s instead of polling 0x6061.
        pv_cfg = ProfileVelocityConfig(
            acceleration=self._cfg.acceleration,
            deceleration=self._cfg.deceleration,
            quick_stop_decel=self._cfg.quick_stop_decel,
            verify_mode=False,
            mode_settle_s=self._cfg.mode_settle_s,
        )
        self._pv = ProfileVelocity(od, config=pv_cfg)

        self._state = JogState(active=False, velocity=0, last_update_s=0.0, deadline_s=0.0)
        self._watch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._mode_ready: bool = False  # True after first successful ensure_mode+configure

    @property
    def state(self) -> JogState:
        return self._state

    async def press(self, velocity: int, *, ttl_s: float | None = None) -> None:
        """Start jog with given signed velocity.

        Args:
            velocity: Signed target velocity in drive units/s.
            ttl_s: Optional TTL override in seconds.  When ``None`` the default
                   from ``JogConfig.ttl_s`` is used.

        On first call: ensure_mode + configure (~300ms).
        On re-press (mode_ready=True): skip init, just set velocity (~10ms).
        """
        async with self._lock:
            await self._ensure_ready_for_motion()
            if not self._mode_ready:
                await self._pv.ensure_mode()
                await self._pv.configure()
                self._mode_ready = True
            await self._pv.set_target_velocity(int(velocity))
            await self._pv.latch_new_setpoint()
            effective_ttl = ttl_s if ttl_s is not None else self._cfg.ttl_s
            now = monotonic_s()
            self._state = JogState(
                active=True,
                velocity=int(velocity),
                last_update_s=now,
                deadline_s=now + float(effective_ttl),
            )
            self._ensure_watchdog()
            _LOGGER.info("Jog: press velocity=%d ttl_s=%s", velocity, effective_ttl)

    async def keepalive(self, *, velocity: int | None = None, ttl_s: float | None = None) -> None:
        """Refresh TTL and optionally update velocity while button is held.

        Args:
            velocity: Optional new velocity.  When ``None`` the current velocity
                      is kept.
            ttl_s: Optional TTL override in seconds.  When ``None`` the default
                   from ``JogConfig.ttl_s`` is used.
        """
        async with self._lock:
            if not self._state.active:
                # If keepalive arrives late, ignore silently; caller can decide to press again.
                return
            if velocity is not None and int(velocity) != int(self._state.velocity):
                await self._pv.set_target_velocity(int(velocity))
                await self._pv.latch_new_setpoint()
                v = int(velocity)
            else:
                v = int(self._state.velocity)

            effective_ttl = ttl_s if ttl_s is not None else self._cfg.ttl_s
            now = monotonic_s()
            self._state = JogState(active=True, velocity=v, last_update_s=now, deadline_s=now + float(effective_ttl))

    async def release(self) -> None:
        """Stop jog explicitly (button up)."""
        _LOGGER.info("Jog: release")
        async with self._lock:
            await self._stop_locked()

    async def watchdog_tick(self) -> None:
        """One watchdog tick: stop jog if TTL expired.

        You can call this from your own scheduler instead of running our task.
        """
        async with self._lock:
            if not self._state.active:
                return
            if monotonic_s() >= self._state.deadline_s:
                await self._stop_locked()

    async def invalidate_mode(self) -> None:
        """Invalidate cached mode state so next press() re-initializes.

        Must be called instead of directly mutating ``_mode_ready`` to
        honour the internal ``_lock`` invariant.
        """
        async with self._lock:
            self._mode_ready = False

    async def close(self) -> None:
        """Stop jogging and cancel watchdog task."""
        async with self._lock:
            await self._stop_locked()
            self._mode_ready = False
        t = self._watch_task
        if t is not None:
            t.cancel()
        self._watch_task = None

    # -----------------------
    # Internals
    # -----------------------
    def _ensure_watchdog(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            return
        self._watch_task = asyncio.create_task(self._watchdog_loop(), name="dryve-jog-watchdog")

    async def _watchdog_loop(self) -> None:
        interval = max(0.01, float(self._cfg.watch_interval_s))
        _consecutive_failures = 0
        try:
            while self._state.active:
                await asyncio.sleep(interval)
                # Abort-aware: if abort_event is set (stop() called), force-stop jog
                if self._abort is not None and self._abort.is_set():
                    _LOGGER.info("Jog watchdog: abort event detected, force-stopping")
                    async with self._lock:
                        await self._stop_locked()
                    return
                # Exit if jog was released while we were sleeping
                if not self._state.active:
                    return
                try:
                    await self.watchdog_tick()
                    _consecutive_failures = 0
                except asyncio.CancelledError:
                    raise  # propagate cancellation
                except Exception:
                    _consecutive_failures += 1
                    if _consecutive_failures >= 5:
                        _LOGGER.error(
                            "Jog watchdog degraded: %d consecutive failures",
                            _consecutive_failures, exc_info=True,
                        )
                    else:
                        _LOGGER.warning(
                            "Jog watchdog tick error (%d consecutive)",
                            _consecutive_failures, exc_info=True,
                        )
        except asyncio.CancelledError:
            return

    async def _stop_locked(self) -> None:
        if not self._state.active:
            return
        if self._cfg.stop_mode == "velocity_zero":
            await self._pv.stop_velocity_zero()
        else:
            # HALT is optional/drive-dependent; keep velocity_zero as primary.
            await self._pv.halt(enabled=True)
            await self._pv.stop_velocity_zero()
            await self._pv.halt(enabled=False)

        now = monotonic_s()
        self._state = JogState(active=False, velocity=0, last_update_s=now, deadline_s=now)
        # Don't reset _mode_ready here — drive stays in PV mode after stop.
        # This allows warm re-start without 300ms ensure_mode delay.
        # _mode_ready is only invalidated by close() (reconnect/cleanup).
        _LOGGER.debug("Jog: stopped")

    async def _ensure_ready_for_motion(self) -> None:
        if not self._cfg.require_operation_enabled:
            return
        sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        flags = decode_statusword(sw)
        if not flags.get("operation_enabled", False):
            raise RuntimeError(f"Drive not in Operation Enabled; statusword=0x{int(sw) & 0xFFFF:04X}")
        if flags.get("fault", False):
            raise RuntimeError(f"Drive in fault; statusword=0x{int(sw) & 0xFFFF:04X}")
