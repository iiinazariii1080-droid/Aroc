from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..cia402.bits import bit_is_set as _bit
from ..od.controlword import cw_enable_operation, cw_pulse_new_set_point
from ..od.indices import ODIndex
from ..protocol.accessor import AsyncODAccessor
from ..protocol.exceptions import MotionAborted
from ..transport.clock import monotonic_s

_LOGGER = logging.getLogger(__name__)

MODE_HOMING = 6


@dataclass(frozen=True, slots=True)
class HomingConfig:
    """Homing configuration.

    Notes:
    - Homing method is drive-specific (see drive manual / DS402 table).
    - Homing speeds (0x6099) commonly use subindex 1 (speed during search)
      and subindex 2 (speed during zero search).
    """

    method: int = 35  # placeholder; must be set to your actual method
    skip_method_write: bool = True  # 0x6098 is RO on dryve D1 (configured via CPG)
    speed_search: int | None = None       # 0x6099:01
    speed_switch: int | None = None       # 0x6099:02
    acceleration: int | None = None       # 0x609A

    poll_interval_s: float = 0.05
    timeout_s: float = 60.0
    system_cycle_delay_s: float = 0.01  # Explicit system cycle delay (default 10ms, typical drive cycle: 1-5ms)

    verify_mode: bool = False
    mode_set_timeout_s: float = 1.0

    def __post_init__(self) -> None:
        if self.system_cycle_delay_s < 0.001:
            raise ValueError(f"system_cycle_delay_s must be >= 0.001, got {self.system_cycle_delay_s}")


@dataclass(frozen=True, slots=True)
class HomingResult:
    attained: bool
    error: bool
    statusword: int


class Homing:
    """Homing mode helper (6060=6)."""

    def __init__(self, od: AsyncODAccessor, *, config: HomingConfig | None = None,
                 abort_event: asyncio.Event | None = None) -> None:
        self._od = od
        self._cfg = config or HomingConfig()
        self._abort: asyncio.Event | None = abort_event

    async def ensure_mode(self) -> None:
        await self._od.write_u8(int(ODIndex.MODES_OF_OPERATION), MODE_HOMING, 0)
        if not self._cfg.verify_mode:
            return
        deadline = monotonic_s() + float(self._cfg.mode_set_timeout_s)
        while True:
            mode_disp = await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            if mode_disp == MODE_HOMING:
                return
            if monotonic_s() >= deadline:
                raise TimeoutError(f"Timeout waiting for mode display == {MODE_HOMING}")
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def configure(self, *, method: int | None = None, speed_search: int | None = None, speed_switch: int | None = None, acceleration: int | None = None) -> None:
        m = self._cfg.method if method is None else method
        s1 = self._cfg.speed_search if speed_search is None else speed_search
        s2 = self._cfg.speed_switch if speed_switch is None else speed_switch
        acc = self._cfg.acceleration if acceleration is None else acceleration

        # 0x6098 Homing method: read-only on dryve D1 (must be configured via CPG web UI).
        # On drives where it is writable, set skip_method_write=False in HomingConfig.
        if not self._cfg.skip_method_write:
            await self._od.write_u8(int(ODIndex.HOMING_METHOD), int(m) & 0xFF, 0)

        if s1 is not None:
            await self._od.write_u32(int(ODIndex.HOMING_SPEEDS), int(s1), 1)
        if s2 is not None:
            await self._od.write_u32(int(ODIndex.HOMING_SPEEDS), int(s2), 2)
        if acc is not None:
            await self._od.write_u32(int(ODIndex.HOMING_ACCELERATION), int(acc), 0)

    async def start(self) -> None:
        """Start homing (pulse NEW_SET_POINT bit in Controlword).
        
        Per manual: start command via bit4 should not be set until required objects
        are configured, and it's recommended to schedule one cycle send/receive as
        a delay before setting start to ensure reliable data adoption.
        
        We ensure this by reading statusword after configuration and before start.
        """
        # Barrier cycle: per manual, wait one system cycle after configuration before start
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
        # Pulse new_setpoint while preserving hold bits (0..3)
        set_word, clear_word = cw_pulse_new_set_point(base)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)
        _LOGGER.info("Homing: start command issued")

    async def run(self, *, timeout_s: float | None = None) -> HomingResult:
        """Configure and perform homing, then wait for completion.

        Completion conditions vary between drives. The DS402 convention:
        - Statusword bit 12: Homing attained
        - Statusword bit 13: Homing error
        - Statusword bit 10: Target reached (often also set at end)
        We use bit 12 as 'attained' and bit 13 as 'error' hint.
        """
        _LOGGER.info("Homing: run started, timeout_s=%s", timeout_s)
        await self.ensure_mode()
        await self.configure()
        await self.start()
        result = await self.wait_done(timeout_s=timeout_s)
        _LOGGER.info("Homing: completed attained=%s error=%s", result.attained, result.error)
        return result

    async def wait_done(self, *, timeout_s: float | None = None) -> HomingResult:
        timeout = self._cfg.timeout_s if timeout_s is None else float(timeout_s)
        deadline = monotonic_s() + timeout

        while True:
            # ── Abort check (highest priority) ──────────────────────
            if self._abort is not None and self._abort.is_set():
                raise MotionAborted("Homing aborted by stop command")

            sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
            # For homing, we interpret bit 12 as "homing attained" (op mode specific)
            attained = _bit(sw, 12)
            # DS402 commonly uses bit 13 as "homing error" (even if our SWBit labels it differently)
            error = _bit(sw, 13)
            # Some simulators/drives signal homing completion only via target_reached (bit 10)
            # without ever setting bit 12. Accept target_reached as an alternative completion signal.
            target_reached = _bit(sw, 10)
            if attained or error:
                return HomingResult(attained=attained, error=error, statusword=int(sw) & 0xFFFF)
            if target_reached:
                # target_reached without attained — treat as successful homing
                # (common with simulators and some drives that use method 35)
                return HomingResult(attained=True, error=False, statusword=int(sw) & 0xFFFF)
            if monotonic_s() >= deadline:
                raise TimeoutError(f"Timeout waiting for homing done. statusword=0x{int(sw) & 0xFFFF:04X}")
            await asyncio.sleep(self._cfg.poll_interval_s)
