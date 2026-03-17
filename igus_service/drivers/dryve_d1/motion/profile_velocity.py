from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

from ..od.controlword import (
    CWBit,
    cw_clear_bits,
    cw_enable_operation,
    cw_pulse_new_set_point,
    cw_set_bits,
)
from ..od.indices import ODIndex
from ..od.statusword import decode_statusword
from ..protocol.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s


@dataclass(frozen=True, slots=True)
class ProfileVelocityConfig:
    """Configuration for Profile Velocity operations."""

    acceleration: int | None = None      # OD 0x6083 (typically UINT32)
    deceleration: int | None = None      # OD 0x6084 (typically UINT32)
    quick_stop_decel: int | None = None  # OD 0x6085 (typically UINT32)

    # Verification: optionally read 0x6061 to confirm mode. Not all drives/gateways update 0x6061
    # in time (e.g. Transaction ID mismatch can return stale reads). Use verify_mode=False for jog
    # and rely on mode_settle_s instead.
    verify_mode: bool = False
    poll_interval_s: float = 0.05
    mode_set_timeout_s: float = 3.0  # When verify_mode=True: wait up to this for 0x6061 == 3
    mode_settle_s: float = 0.25  # When verify_mode=False: delay after writing 0x6060 before continuing


MODE_PROFILE_VELOCITY = 3  # CiA 402 Profile Velocity Mode


class ProfileVelocity:
    """Profile Velocity mode helper (6060=3).

    This class does not manage CiA402 state transitions (Enable Operation).
    It assumes the drive is already in Operation Enabled when you command motion.
    """

    def __init__(
        self,
        od: AsyncODAccessor,
        *,
        config: ProfileVelocityConfig | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> None:
        self._od = od
        self._cfg = config or ProfileVelocityConfig()
        self._abort: asyncio.Event | None = abort_event

    async def ensure_mode(self) -> None:
        _LOGGER.debug("PV: setting mode=%d", MODE_PROFILE_VELOCITY)
        await self._od.write_u8(int(ODIndex.MODES_OF_OPERATION), MODE_PROFILE_VELOCITY, 0)
        if not self._cfg.verify_mode:
            # Rely on fixed delay instead of 0x6061 (avoids timeout when gateway returns stale 0x6061)
            await asyncio.sleep(max(0.01, float(self._cfg.mode_settle_s)))
            return

        # Give the drive and gateway time to apply the mode change before polling 0x6061
        await asyncio.sleep(self._cfg.poll_interval_s)

        deadline = monotonic_s() + float(self._cfg.mode_set_timeout_s)
        while True:
            mode_disp = await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            if mode_disp == MODE_PROFILE_VELOCITY:
                return
            if monotonic_s() >= deadline:
                raise TimeoutError(f"Timeout waiting for mode display == {MODE_PROFILE_VELOCITY}")
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def configure(self, *, acceleration: int | None = None, deceleration: int | None = None, quick_stop_decel: int | None = None) -> None:
        acc = self._cfg.acceleration if acceleration is None else acceleration
        dec = self._cfg.deceleration if deceleration is None else deceleration
        qsd = self._cfg.quick_stop_decel if quick_stop_decel is None else quick_stop_decel

        if acc is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_ACCELERATION), int(acc), 0)
        if dec is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_DECELERATION), int(dec), 0)
        if qsd is not None:
            await self._od.write_u32(int(ODIndex.QUICK_STOP_DECELERATION), int(qsd), 0)

    async def set_target_velocity(self, velocity: int) -> None:
        """Set Target Velocity (0x60FF). Value is typically INT32."""
        _LOGGER.debug("PV: set_target_velocity=%d", velocity)
        await self._od.write_i32(int(ODIndex.TARGET_VELOCITY), int(velocity), 0)

    async def latch_new_setpoint(self) -> None:
        """Pulse NEW_SET_POINT (Controlword bit 4) so the drive accepts the new target velocity.
        Some drives (e.g. Dryve D1) require this in Profile Velocity mode to start motion."""
        base = cw_enable_operation()
        set_word, clear_word = cw_pulse_new_set_point(base)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)

    async def stop_velocity_zero(self) -> None:
        """Stop by commanding target velocity to 0."""
        _LOGGER.info("PV: stop (velocity → 0)")
        await self.set_target_velocity(0)

    async def stop(self) -> None:
        """Stop movement in Profile Velocity mode using normal deceleration.
        
        According to the manual, "Stop" command stops movement with a pre-set rate
        of deceleration (Profile Deceleration, 0x6084). This is different from
        Quick Stop which uses Quick Stop Deceleration (0x6085).
        
        In Profile Velocity mode, the standard way to stop with normal deceleration
        is to set target velocity to 0. The drive will decelerate using the configured
        Profile Deceleration value.
        """
        await self.set_target_velocity(0)

    async def halt(self, *, enabled: bool = True) -> None:
        """Optionally use Controlword HALT bit (bit 8).

        Not all drives implement HALT in velocity mode consistently.
        Prefer stop_velocity_zero() for jog semantics.

        This method writes Controlword, thus it includes hold bits (0..3).
        Per manual: after Operation Enabled, bits 0..3 must always be sent.
        """
        if self._abort is not None and self._abort.is_set():
            _LOGGER.debug("PV: halt skipped — abort event active")
            return
        # Per manual: after Operation Enabled, bits 0..3 must always be sent
        # Start with base containing hold bits (0x000F)
        base = cw_enable_operation()  # 0x000F = bits 0,1,2,3 set
        word = cw_set_bits(base, CWBit.HALT) if enabled else cw_clear_bits(base, CWBit.HALT)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(word) & 0xFFFF, 0)

    async def is_target_reached_flag(self) -> bool:
        """Convenience: read Statusword and return 'target_reached' bit.

        In velocity mode this bit may not be meaningful; do not rely on it for motion detection.
        """
        sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        return bool(decode_statusword(sw).get("target_reached"))
