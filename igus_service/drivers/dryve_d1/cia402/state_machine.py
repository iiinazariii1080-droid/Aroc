"""CiA 402 state machine transitions for dryve D1.

The dryve D1 manual requires:
- DI7 'Enable' must be HIGH (Statusword bit 9 'Remote' == 1) for the state machine to run.
- After reaching 'Operation enabled', Controlword bits 0..3 must be sent with each Controlword telegram
  to maintain the state.

This module implements an async state machine runner on top of a minimal OD accessor interface.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

from ..od.controlword import (
    CWBit,
    cw_disable_voltage,
    cw_enable_operation,
    cw_fault_reset,
    cw_quick_stop as _cw_quick_stop,
    cw_set_bits,
    cw_shutdown,
    cw_switch_on,
)
from ..od.indices import ODIndex
from ..od.statusword import CiA402State, SWBit, infer_cia402_state
from ..protocol.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .bits import bit_is_set
from .dominance import require_remote_enabled

_U16_MASK = 0xFFFF
_INVALID_BOOT_STATE = 0x2704  # documented as invalid state (restart required)


class StateMachineError(RuntimeError):
    """Base error for state machine failures."""


class StateMachineTimeout(StateMachineError):
    """Raised when the drive does not reach the expected state within the timeout."""


class InvalidBootStateError(StateMachineError):
    """Raised when the drive is detected in a documented invalid state (restart required)."""


@dataclass(frozen=True, slots=True)
class StateMachineConfig:
    poll_interval_s: float = 0.05
    step_timeout_s: float = 5.0
    fault_reset_timeout_s: float = 5.0
    require_remote: bool = True


def _fmt_u16(x: int) -> str:
    return f"0x{int(x) & _U16_MASK:04X}"


def _ensure_hold_bits(controlword: int) -> int:
    """Ensure bits 0..3 are present (hold) to maintain Operation Enabled."""
    # bits: switch on, enable voltage, quick stop, enable operation
    return cw_set_bits(int(controlword), CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION)


class CiA402StateMachine:
    """Async CiA 402 state machine runner for dryve D1."""

    def __init__(self, od: AsyncODAccessor, *, config: StateMachineConfig | None = None) -> None:
        self._od = od
        self._cfg = config or StateMachineConfig()

    async def read_statusword(self) -> int:
        sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        sw_u16 = int(sw) & _U16_MASK
        if sw_u16 == _INVALID_BOOT_STATE:
            raise InvalidBootStateError(
                "Drive is in documented invalid state 0x2704 (likely from previous non-CiA402 mode). "
                "Restart the motor controller."
            )
        if self._cfg.require_remote:
            require_remote_enabled(sw_u16)
        return sw_u16

    async def write_controlword(self, value: int) -> None:
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(value) & _U16_MASK, 0)

    async def current_state(self) -> CiA402State:
        sw = await self.read_statusword()
        return infer_cia402_state(sw)

    async def _wait_for_states(self, desired: set[CiA402State], *, timeout_s: float | None = None) -> CiA402State:
        timeout = self._cfg.step_timeout_s if timeout_s is None else timeout_s
        deadline = monotonic_s() + float(timeout)
        last_state = CiA402State.UNKNOWN

        while True:
            loop_time = monotonic_s()
            sw = await self.read_statusword()
            last_state = infer_cia402_state(sw)
            if last_state in desired:
                return last_state
            if loop_time >= deadline:
                raise StateMachineTimeout(
                    f"Timeout waiting for states {sorted(s.value for s in desired)}; last_state={last_state.value}, statusword={_fmt_u16(sw)}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)

    # -----------------------
    # Basic transitions
    # -----------------------
    async def disable_voltage(self) -> None:
        await self.write_controlword(cw_disable_voltage())
        # Drive should end up in "Switch on disabled" typically, but we don't hard-enforce here.

    async def quick_stop(self) -> None:
        """Request quick stop.

        Per CiA402: clear bit 2 while maintaining hold bits (0,1,3).
        The drive should transition to Quick Stop Active state.
        """
        await self.write_controlword(_cw_quick_stop())

        try:
            await self._wait_for_states({CiA402State.QUICK_STOP_ACTIVE}, timeout_s=2.0)
        except StateMachineTimeout:
            _LOGGER.warning("Quick stop: did not reach QUICK_STOP_ACTIVE within 2s")

    async def shutdown(self) -> None:
        await self.write_controlword(cw_shutdown())
        await self._wait_for_states({CiA402State.READY_TO_SWITCH_ON, CiA402State.SWITCH_ON_DISABLED})

    async def switch_on(self) -> None:
        await self.write_controlword(cw_switch_on())
        await self._wait_for_states({CiA402State.SWITCHED_ON, CiA402State.OPERATION_ENABLED})

    async def enable_operation(self) -> None:
        # Per manual, bits 0..3 must be sent permanently after first set.
        await self.write_controlword(_ensure_hold_bits(cw_enable_operation()))
        await self._wait_for_states({CiA402State.OPERATION_ENABLED})

    async def fault_reset(self) -> None:
        """Reset fault according to contract.
        
        Preconditions:
        - Statusword bit 9 (REMOTE) = 1 (required for dryve D1)
        - Current state = FAULT or FAULT_REACTION_ACTIVE
        
        Postconditions:
        - If fault was present: final state ∈ {SWITCH_ON_DISABLED, READY_TO_SWITCH_ON}
        - If no fault: state unchanged (method returns without effect)
        - Statusword: b3=0 (fault bit cleared)
        """
        # Per manual, remote must be enabled for fault reset to work.
        sw = await self.read_statusword()
        if not bit_is_set(sw, SWBit.FAULT):
            return  # not in fault, safe to return without effect

        # Check REMOTE bit (bit 9) - required for dryve D1
        if not bit_is_set(sw, SWBit.REMOTE):
            raise StateMachineError("REMOTE bit (bit 9) must be enabled for fault reset to work")
        
        await self.write_controlword(cw_fault_reset())
        # Many drives require a pulse; send reset then clear it (safe baseline: shutdown).
        # Per contract: fault reset pulse should be at least 100ms
        await asyncio.sleep(max(0.1, self._cfg.poll_interval_s))
        await self.write_controlword(cw_shutdown())
        # Safety: set HALT=1 after reset per manual guidance — prevents
        # uncontrolled motion when later transitioning to Operation Enabled.
        safe_halt = cw_set_bits(cw_shutdown(), CWBit.HALT)
        await self.write_controlword(safe_halt)
        # Wait until fault clears with fault_reset_timeout_s
        await self._wait_for_states({
            CiA402State.SWITCH_ON_DISABLED,
            CiA402State.READY_TO_SWITCH_ON,
            CiA402State.SWITCHED_ON,
            CiA402State.OPERATION_ENABLED,
        }, timeout_s=self._cfg.fault_reset_timeout_s)

    # -----------------------
    # High-level helpers
    # -----------------------
    async def run_to_operation_enabled(self) -> None:
        """Run the state machine to reach 'Operation enabled'.

        This is the standard path:
          - if fault -> reset fault
          - if switch on disabled -> shutdown
          - ready to switch on -> switch on
          - switched on -> enable operation
        """
        st = await self.current_state()

        if st == CiA402State.FAULT or st == CiA402State.FAULT_REACTION_ACTIVE:
            await self.fault_reset()
            st = await self.current_state()

        if st == CiA402State.SWITCH_ON_DISABLED:
            await self.shutdown()
            st = await self.current_state()

        if st == CiA402State.NOT_READY_TO_SWITCH_ON:
            # Often transitions automatically to switch-on-disabled; just wait briefly then proceed.
            await self._wait_for_states({CiA402State.SWITCH_ON_DISABLED, CiA402State.READY_TO_SWITCH_ON})
            st = await self.current_state()

        if st == CiA402State.READY_TO_SWITCH_ON:
            await self.switch_on()
            st = await self.current_state()

        if st == CiA402State.SWITCHED_ON:
            await self.enable_operation()
            return

        if st == CiA402State.OPERATION_ENABLED:
            # Ensure we have hold bits set at least once.
            await self.write_controlword(_ensure_hold_bits(cw_enable_operation()))
            return

        if st == CiA402State.QUICK_STOP_ACTIVE:
            # Bring it back via shutdown -> switch_on -> enable_operation.
            await self.shutdown()
            await self.switch_on()
            await self.enable_operation()
            return

        raise StateMachineError(f"Unhandled CiA402 state: {st.value}")
