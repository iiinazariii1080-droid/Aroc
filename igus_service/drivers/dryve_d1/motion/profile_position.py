from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from ..od.controlword import (
    CWBit,
    cw_clear_bits,
    cw_enable_operation,
    cw_pulse_new_set_point,
    cw_set_bits,
)
from ..od.indices import ODIndex
from ..od.statusword import SWBit, decode_statusword
from ..protocol.exceptions import MotionAborted
from ..transport.clock import monotonic_s


class AsyncODAccessor(Protocol):
    async def read_u16(self, index: int, subindex: int = 0) -> int: ...
    async def read_i8(self, index: int, subindex: int = 0) -> int: ...
    async def read_i32(self, index: int, subindex: int = 0) -> int: ...
    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None: ...

@dataclass(frozen=True, slots=True)
class ProfilePositionConfig:
    """Configuration for Profile Position operations."""

    profile_velocity: int | None = None   # 0x6081 (UINT32, often)
    acceleration: int | None = None       # 0x6083 (UINT32)
    deceleration: int | None = None       # 0x6084 (UINT32)

    poll_interval_s: float = 0.05
    move_timeout_s: float = 30.0
    system_cycle_delay_s: float = 0.01  # B2: Explicit system cycle delay (default 10ms, typical drive cycle: 1-5ms)

    verify_mode: bool = False
    mode_set_timeout_s: float = 1.0
    mode_settle_s: float = 0.3  # Delay after writing mode register when verify_mode=False

MODE_PROFILE_POSITION = 1

def _bit(word: int, bit: int) -> bool:
    return bool((int(word) >> int(bit)) & 1)

class ProfilePosition:
    """Profile Position mode helper (6060=1)."""

    def __init__(self, od: AsyncODAccessor, *, config: ProfilePositionConfig | None = None,
                 abort_event: asyncio.Event | None = None) -> None:
        self._od = od
        self._cfg = config or ProfilePositionConfig()
        self._abort: asyncio.Event | None = abort_event

    async def ensure_mode(self) -> None:
        # Always write mode register and wait for settle — do NOT try to read
        # 0x6061 first, because the dryve D1 gateway can return stale values
        # (documented issue, same approach as PV mode's ensure_mode).
        await self._od.write_u8(int(ODIndex.MODES_OF_OPERATION), MODE_PROFILE_POSITION, 0)
        if not self._cfg.verify_mode:
            await asyncio.sleep(max(0.01, float(self._cfg.mode_settle_s)))
            return
        deadline = monotonic_s() + float(self._cfg.mode_set_timeout_s)
        while True:
            mode_disp = await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            if mode_disp == MODE_PROFILE_POSITION:
                return
            if monotonic_s() >= deadline:
                raise TimeoutError(f"Timeout waiting for mode display == {MODE_PROFILE_POSITION}")
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def configure(self, *, profile_velocity: int | None = None, acceleration: int | None = None, deceleration: int | None = None) -> None:
        pv = self._cfg.profile_velocity if profile_velocity is None else profile_velocity
        acc = self._cfg.acceleration if acceleration is None else acceleration
        dec = self._cfg.deceleration if deceleration is None else deceleration

        if pv is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_VELOCITY), int(pv), 0)
        if acc is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_ACCELERATION), int(acc), 0)
        if dec is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_DECELERATION), int(dec), 0)

    async def move_to(self, target_position: int, *, relative: bool = False, immediate: bool = True, timeout_s: float | None = None) -> None:
        """Command a move to target_position and wait until 'target reached' bit is set.

        Assumptions:
        - Drive is in Operation Enabled
        - Position units match your scaling layer

        Args:
            target_position: desired position (INT32)
            relative: if True, interpret as relative move (CW bit 6)
            immediate: if True, set CW bit 5 (change set immediately) when pulsing new set-point
            timeout_s: override default move timeout
        
        Raises:
            ValueError: If relative=False and target_position < 0 (absolute position cannot be negative per M4 requirement)
        """
        # M4: Validate absolute position cannot be negative
        if not relative and target_position < 0:
            raise ValueError(
                f"Absolute position cannot be negative (relative=False, target_position={target_position}). "
                "Per manual requirement: if Absolute (bit6=0), position must be >= 0 after homing."
            )
        
        await self.ensure_mode()
        await self.configure()
        
        await self._od.write_i32(int(ODIndex.TARGET_POSITION), int(target_position), 0)
        
        # B2: Barrier cycle: per manual, wait one system cycle after configuration before start
        # Per manual requirement: after parameterizing mode objects, wait one system cycle
        # before sending Start Command via Controlword bit 4.
        # We ensure this by: (1) reading statusword as a round-trip barrier to ensure
        # the drive has processed parameter changes, (2) adding explicit system cycle delay.
        await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        # Explicit system cycle delay (typical drive cycle: 1-5ms, use configurable delay)
        await asyncio.sleep(self._cfg.system_cycle_delay_s)
        # Per manual: after Operation Enabled, bits 0..3 must always be sent
        # Start with base containing hold bits (0x000F)
        base = cw_enable_operation()  # 0x000F = bits 0,1,2,3 set
        
        if immediate:
            base = cw_set_bits(base, CWBit.CHANGE_SET_IMMEDIATELY)
        else:
            base = cw_clear_bits(base, CWBit.CHANGE_SET_IMMEDIATELY)

        if relative:
            base = cw_set_bits(base, CWBit.ABS_REL)
        else:
            base = cw_clear_bits(base, CWBit.ABS_REL)

        # Pulse new_setpoint while preserving hold bits (0..3)
        set_word, clear_word = cw_pulse_new_set_point(base)

        # Rising edge on NEW_SET_POINT
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
        # Keep bit4 high for at least one system cycle so the drive latches
        # the start command reliably (avoids missed pulse on some firmware).
        await asyncio.sleep(self._cfg.system_cycle_delay_s)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)

        # M3: PP handshake - after Start, wait for command acknowledgment
        # Per manual: after Start (bit4), drive resets bit10 and sets bit12,
        # then bit12 clears itself. We wait for bit10==0 OR bit12==1 to confirm command acceptance.
        ack_seen = await self._wait_start_acknowledgment()

        # Pass ack_seen so wait_target_reached knows whether bit10 was
        # observed to transition to 0.  If ack was never seen (e.g. stale
        # bit10=1 from previous homing/motion), wait_target_reached will
        # first wait for bit10 to clear before waiting for the rising edge.
        await self.wait_target_reached(timeout_s=timeout_s, _ack_seen=ack_seen)

    async def move_to_position(
        self,
        *,
        target_position: int,
        profile_velocity: int,
        profile_accel: int,
        profile_decel: int,
        timeout_s: float | None = None,
    ) -> None:
        """Move to target position with specified velocity, acceleration, and deceleration.
        
        This is a convenience method that configures motion parameters and then moves.
        Note: ensure_mode() is called inside move_to(), so we only call configure() here
        to set velocity/accel/decel before the move.
        """
        await self.configure(
            profile_velocity=profile_velocity,
            acceleration=profile_accel,
            deceleration=profile_decel,
        )
        await self.move_to(target_position=target_position, timeout_s=timeout_s)

    async def halt(self, *, enabled: bool = True) -> None:
        """Halt movement in Profile Position mode using Controlword HALT bit (bit 8).
        
        In Profile Position mode, the HALT bit (bit 8) is typically used to stop
        movement immediately, rather than quick_stop. This is the standard CiA402
        method for stopping motion in profile modes.
        
        Args:
            enabled: If True, set HALT bit to stop movement. If False, clear HALT bit.
        """
        # Per manual: after Operation Enabled, bits 0..3 must always be sent
        # Start with base containing hold bits (0x000F)
        base = cw_enable_operation()  # 0x000F = bits 0,1,2,3 set
        word = cw_set_bits(base, CWBit.HALT) if enabled else cw_clear_bits(base, CWBit.HALT)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(word) & 0xFFFF, 0)

    async def stop(self) -> None:
        """Stop movement in Profile Position mode using normal deceleration.
        
        According to the manual, "Stop" command stops movement with a pre-set rate
        of deceleration (Profile Deceleration, 0x6084). This is different from
        Quick Stop which uses Quick Stop Deceleration (0x6085).
        
        In Profile Position mode, the standard way to stop with normal deceleration
        is to use the HALT bit (bit 8). The drive will decelerate using the configured
        Profile Deceleration value.
        
        Note: This method uses HALT bit which is the standard CiA402 method for
        stopping motion in profile modes with normal deceleration.
        """
        await self.halt(enabled=True)

    async def _wait_start_acknowledgment(self, *, timeout_s: float = 0.5) -> bool:
        """Wait for Start command acknowledgment (M3: PP handshake).
        
        Per manual: after Start (bit4), the drive should:
        - Reset bit10 (target_reached) OR
        - Set bit12 (op_mode_specific) to confirm command acceptance
        
        Returns True if acknowledgment was observed (bit10 transitioned to 0
        or motion already completed),
        False if timed out without seeing bit10 clear.
        """
        deadline = monotonic_s() + timeout_s
        while True:
            sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
            target_reached = _bit(sw, int(SWBit.TARGET_REACHED))
            op_mode_specific = _bit(sw, int(SWBit.OP_MODE_SPECIFIC))
            # Command acknowledged if: bit10 cleared OR bit12 set
            if not target_reached or op_mode_specific:
                return True
            
            if monotonic_s() >= deadline:
                # Timeout: bit10 never cleared.  This can happen when the
                # move completes faster than one poll cycle (the transient
                # bit10=0 was never observed).  Check if actual position
                # already matches the target — if so, declare success.
                target_pos = await self._od.read_i32(
                    int(ODIndex.TARGET_POSITION), 0)
                actual_pos = await self._od.read_i32(
                    int(ODIndex.POSITION_ACTUAL_VALUE), 0)
                if target_reached and abs(actual_pos - target_pos) <= 1:
                    return True  # move completed during the ack window
                return False
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def wait_target_reached(self, *, timeout_s: float | None = None,
                                  _ack_seen: bool = True) -> None:
        timeout = self._cfg.move_timeout_s if timeout_s is None else float(timeout_s)
        deadline = monotonic_s() + timeout

        async def _read_mode_display_safe() -> int | None:
            try:
                return await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            except Exception:
                return None

        # If the PP handshake never saw bit10 clear (stale target_reached from
        # a previous motion), we MUST wait for bit10=0 first, then wait for the
        # genuine bit10=1 rising edge.  Without this, a stale bit10=1 after
        # homing or a previous move causes an instant false "target reached".
        if not _ack_seen:
            while True:
                if self._abort is not None and self._abort.is_set():
                    raise MotionAborted("Motion aborted by stop command")
                sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
                if not _bit(sw, int(SWBit.TARGET_REACHED)):
                    break  # bit10 finally cleared → now wait for real rising edge
                if _bit(sw, int(SWBit.FAULT)):
                    decoded = decode_statusword(sw)
                    raise RuntimeError(
                        f"Fault detected waiting for target_reached to clear. "
                        f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}"
                    )
                if monotonic_s() >= deadline:
                    decoded = decode_statusword(sw)
                    mode_display = await _read_mode_display_safe()
                    raise TimeoutError(
                        f"Timeout: target_reached never cleared after new set-point. "
                        f"statusword=0x{int(sw) & 0xFFFF:04X}, mode_display={mode_display}, flags={decoded}"
                    )
                await asyncio.sleep(self._cfg.poll_interval_s)

        while True:
            # ── Abort check (highest priority) ──────────────────────
            if self._abort is not None and self._abort.is_set():
                raise MotionAborted("Motion aborted by stop command")

            loop_time = monotonic_s()
            sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
            if _bit(sw, int(SWBit.TARGET_REACHED)):
                return
            
            # Check for fault condition
            if _bit(sw, int(SWBit.FAULT)):
                decoded = decode_statusword(sw)
                raise RuntimeError(
                    f"Fault detected while waiting for target reached. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}"
                )
            
            if loop_time >= deadline:
                # Provide more diagnostic information on timeout
                decoded = decode_statusword(sw)
                target_pos = await self._od.read_i32(int(ODIndex.TARGET_POSITION), 0)
                actual_pos = await self._od.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE), 0)
                mode_display = await _read_mode_display_safe()
                position_error = abs(actual_pos - target_pos)
                raise TimeoutError(
                    f"Timeout waiting for target reached after {timeout:.1f}s. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, "
                    f"mode_display={mode_display}, "
                    f"target_reached={decoded.get('target_reached', False)}, "
                    f"op_mode_specific={decoded.get('op_mode_specific', False)}, "
                    f"target_position={target_pos}, actual_position={actual_pos}, "
                    f"position_error={position_error}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)
