"""Idle shutdown timer mixin for DryveD1.

Schedules disable_voltage after a configurable delay once motion stops.
Two-phase design:
  Phase 1 (delay):  TimerHandle from loop.call_later()  — cancel is synchronous
  Phase 2 (action): Task running _idle_shutdown_action() — cancel via Task.cancel()
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..cia402.state_machine import CiA402State, CiA402StateMachine
    from ..motion.jog import JogController

_LOGGER = logging.getLogger(__name__)


class IdleShutdownMixin:
    """Manages delayed disable_voltage after motion stops."""

    # Provided by DryveD1.__init__
    _idle_shutdown_handle: asyncio.TimerHandle | None
    _idle_shutdown_task: asyncio.Task[None] | None
    _idle_shutdown_delay_s: float
    _sm: CiA402StateMachine | None
    _jog: JogController | None

    # disable_voltage() is provided by MotionCommandsMixin — do NOT
    # redeclare here as a stub, or it shadows the real implementation
    # due to MRO ordering (IdleShutdownMixin before MotionCommandsMixin).

    def _cancel_idle_shutdown_timer(self) -> None:
        """Cancel idle shutdown in whichever phase it's in."""
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
        self._idle_shutdown_task = asyncio.get_running_loop().create_task(
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
