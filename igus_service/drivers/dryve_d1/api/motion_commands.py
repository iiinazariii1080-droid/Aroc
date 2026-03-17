"""Motion command mixin for DryveD1.

Provides high-level motion commands: move_to_position, jog_start/update/stop,
home, stop/quick_stop, fault_reset, enable_operation, and shared preconditions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from ..cia402.dominance import PreconditionFailed
from ..od.controlword import (
    CWBit,
    cw_clear_bits,
    cw_disable_voltage,
    cw_enable_operation,
    cw_set_bits,
)
from ..od.indices import ODIndex

if TYPE_CHECKING:
    from ..cia402.state_machine import CiA402StateMachine
    from ..motion.homing import Homing, HomingResult
    from ..motion.jog import JogController
    from ..motion.profile_position import ProfilePosition
    from ..motion.profile_velocity import ProfileVelocity
    from .drive import DryveD1Config

_LOGGER = logging.getLogger(__name__)


class MotionCommandsMixin:
    """Motion commands: move, jog, home, stop, fault_reset."""

    # Provided by DryveD1
    _cfg: DryveD1Config
    _sm: CiA402StateMachine | None
    _pp: ProfilePosition | None
    _pv: ProfileVelocity | None
    _homing: Homing | None
    _jog: JogController | None
    _abort_event: asyncio.Event
    _abort_token: str

    # Provided by OdAccessorMixin
    async def read_u16(self, index: int, subindex: int = 0) -> int: ...
    async def read_i8(self, index: int, subindex: int = 0) -> int: ...
    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None: ...

    # Provided by StatusQueriesMixin
    async def get_status(self) -> dict[str, bool]: ...
    async def get_status_live(self) -> dict[str, bool]: ...
    async def get_position_live(self) -> int: ...
    async def is_homed(self) -> bool: ...
    def _resolve_position_limits(self) -> tuple[int, int]: ...

    # Provided by IdleShutdownMixin
    def _cancel_idle_shutdown_timer(self) -> None: ...
    def _schedule_idle_shutdown(self) -> None: ...

    # Provided by DryveD1
    @property
    def is_connected(self) -> bool: ...

    # ---- require helpers ----

    def _require(self, component: Any, name: str) -> Any:
        """Return *component* or raise RuntimeError if None (not connected)."""
        if component is None:
            raise RuntimeError(f"Not connected ({name} unavailable)")
        return component

    def _require_sm(self) -> CiA402StateMachine:
        return self._require(self._sm, "state machine")

    def _require_pp(self) -> ProfilePosition:
        return self._require(self._pp, "profile position")

    def _require_pv(self) -> ProfileVelocity:
        return self._require(self._pv, "profile velocity")

    def _require_homing(self) -> Homing:
        return self._require(self._homing, "homing")

    def _require_jog(self) -> JogController:
        return self._require(self._jog, "jog controller")

    # ---- high-level controls ----

    async def enable_operation(self) -> None:
        """Enable operation (bring drive to OPERATION_ENABLED state)."""
        sm = self._require_sm()
        await sm.run_to_operation_enabled()

    async def disable_voltage(self) -> None:
        """Write CW=0x0000 to transition to SWITCH_ON_DISABLED.

        Suppresses keepalive I/O to prevent dryve D1 firmware interference
        (confirmed on real hardware: concurrent reads block CW state transition).
        """
        _LOGGER.debug("disable_voltage: entering")
        if self._session is not None:
            self._session.suppress_keepalive(2.0)
        _LOGGER.debug("disable_voltage: writing CW=0x0000")
        await self.write_u16(int(ODIndex.CONTROLWORD), cw_disable_voltage(), 0)
        _LOGGER.debug("disable_voltage: write complete, waiting for transition")
        await asyncio.sleep(0.3)

    async def quick_stop(self, *, op_id: str | None = None) -> None:
        """Request quick stop (immediate deceleration)."""
        await self._execute_stop(mode="quick", op_id=op_id)

    async def stop(self, *, op_id: str | None = None) -> None:
        """Stop movement using normal (mode-aware) deceleration."""
        await self._execute_stop(mode="normal", op_id=op_id)

    async def _execute_stop(
        self,
        mode: str,
        op_id: str | None = None,
    ) -> None:
        """Unified stop implementation. Never raises."""
        try:
            op_id = op_id or uuid.uuid4().hex[:8]
            label = "quick_stop" if mode == "quick" else "stop"

            self._abort_token = uuid.uuid4().hex
            self._abort_event.set()
            _LOGGER.info("%s[%s]: abort_event set, token rotated", label, op_id)

            halt_ok = await self._halt_motor()
            if not halt_ok:
                _LOGGER.warning("%s[%s]: HALT write failed, continuing with mode-aware stop", label, op_id)

            try:
                status = await self.get_status()
                if not (status.get("operation_enabled", False) or status.get("quick_stop", False)):
                    _LOGGER.info("%s[%s]: skipped (drive not enabled)", label, op_id)
                    return

                sm = self._require_sm()

                if mode == "quick":
                    await sm.quick_stop()
                else:
                    mode_display = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
                    current_mode = int(mode_display)
                    if current_mode == 1 and self._pp is not None:
                        await self._pp.stop()
                    elif current_mode == 3 and self._pv is not None:
                        await self._pv.stop()
                    else:
                        await sm.quick_stop()

                _LOGGER.info("%s[%s]: completed", label, op_id)
            except Exception:
                _LOGGER.warning("%s[%s]: best-effort path failed", label, op_id, exc_info=True)
        except Exception:
            _LOGGER.error(
                "_execute_stop: unexpected top-level failure (mode=%s, op_id=%s)",
                mode, op_id, exc_info=True,
            )

    async def _halt_motor(self) -> bool:
        """Write HALT bit (bit 8) to controlword to physically stop the motor.

        Returns True if write succeeded, False if both attempts failed.
        """
        halt_cw = cw_set_bits(cw_enable_operation(), CWBit.HALT)
        for attempt in range(2):
            try:
                await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cw) & 0xFFFF, 0)
                return True
            except Exception:
                if attempt == 0:
                    _LOGGER.warning("HALT write failed (attempt 1/2), retrying", exc_info=True)
                else:
                    _LOGGER.error("HALT write failed (attempt 2/2) — motor may not have stopped", exc_info=True)
        return False

    # ---- fault reset ----

    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None:
        """Reset fault and optionally perform full recovery to Operation Enabled."""
        op_id = op_id or uuid.uuid4().hex[:8]
        _LOGGER.info("fault_reset[%s]: requested recover=%s", op_id, recover)
        sm = self._require_sm()

        status_before = await self.get_status()
        was_in_fault = status_before.get("fault", False)
        await sm.fault_reset()
        # Full recovery procedure if requested
        if recover and was_in_fault:
            await sm.run_to_operation_enabled()
            halt_word = cw_set_bits(cw_enable_operation(), CWBit.HALT)
            await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_word) & 0xFFFF, 0)
        _LOGGER.info("fault_reset[%s]: completed recover=%s was_in_fault=%s", op_id, recover, was_in_fault)

    # ---- shared motion preconditions ----

    async def _check_drive_ready(self, *, op_id: str) -> dict[str, Any]:
        """Live status read, fault gate, and auto-enable."""
        status = await self.get_status_live()
        if status.get("fault", False):
            raise RuntimeError("Drive is in FAULT state. Call fault_reset() first.")
        if not status.get("operation_enabled", False):
            await self.enable_operation()
            status = await self.get_status_live()
        return status

    async def _require_remote(self, status: dict[str, Any], *, op_id: str) -> None:
        """Require Statusword REMOTE bit (bit 9) to be HIGH."""
        if not status.get("remote", False):
            sw = await self.read_u16(int(ODIndex.STATUSWORD))
            raise PreconditionFailed(
                "Remote not enabled: Statusword bit 9 is LOW "
                "(DI7 'Enable' must be HIGH). "
                f"statusword=0x{sw:04X}"
            )

    # ---- move_to_position (decomposed into preparation helpers) ----

    async def _prepare_motion_context(self, op_id: str) -> dict[str, bool]:
        """Prepare the drive for a motion command."""
        if not self.is_connected:
            raise RuntimeError("Not connected")

        self._cancel_idle_shutdown_timer()

        status = await self.get_status_live()
        _LOGGER.info(
            "move_to_position[%s]: status=%s",
            op_id,
            {k: status.get(k) for k in ("operation_enabled", "remote", "fault", "target_reached")},
        )

        try:
            jog = self._require_jog()
            if jog.state.active:
                await jog.release()
                await asyncio.sleep(self._cfg.motion_precheck_delay_s)
                _LOGGER.info("move_to_position[%s]: active jog released before PP move", op_id)
        except Exception:
            _LOGGER.debug("move_to_position[%s]: jog pre-stop skipped/failed", op_id, exc_info=True)

        return status

    _MODE_PP = 1

    async def _ensure_mode_pp(self, status: dict[str, bool], op_id: str) -> dict[str, bool]:
        """Ensure the drive is in OPERATION_ENABLED with PP mode active."""
        need_enable = not status.get("operation_enabled", False)
        wrong_mode = False

        if not need_enable:
            try:
                current_mode = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
                wrong_mode = (current_mode != self._MODE_PP)
            except Exception:
                wrong_mode = True

        if need_enable or wrong_mode:
            sm = self._require_sm()
            if wrong_mode and not need_enable:
                _LOGGER.info(
                    "move_to_position[%s]: mode!=PP, cycling SM for clean transition", op_id,
                )
                await sm.shutdown()

            await self.write_u8(int(ODIndex.MODES_OF_OPERATION), self._MODE_PP, 0)
            await asyncio.sleep(self._cfg.mode_settle_delay_s)
            _LOGGER.debug("move_to_position[%s]: mode=PP written before enable", op_id)

            await self.enable_operation()
            status = await self.get_status_live()
            _LOGGER.info("move_to_position[%s]: enable_operation completed", op_id)

        return status

    def _validate_motion_params(
        self,
        *,
        velocity: int,
        accel: int,
        decel: int,
        timeout_s: float,
    ) -> None:
        """Validate motion parameters against configured limits."""
        limits = self._cfg.drive.limits

        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")

        self._validate_velocity(velocity)

        if accel <= 0:
            raise ValueError(f"accel must be > 0, got {accel}")
        if limits.max_abs_accel is not None and accel > limits.max_abs_accel:
            raise ValueError(f"accel {accel} exceeds max_abs_accel {limits.max_abs_accel}")

        if decel <= 0:
            raise ValueError(f"decel must be > 0, got {decel}")
        if limits.max_abs_decel is not None and decel > limits.max_abs_decel:
            raise ValueError(f"decel {decel} exceeds max_abs_decel {limits.max_abs_decel}")

    def _validate_velocity(self, velocity: int) -> None:
        """Validate velocity against configured limits."""
        if velocity == 0:
            raise ValueError("velocity must be != 0")
        max_abs = self._cfg.drive.limits.max_abs_velocity
        if max_abs is not None and abs(velocity) > max_abs:
            raise ValueError(f"velocity {abs(velocity)} exceeds max_abs_velocity {max_abs}")

    async def _check_jog_boundary(self, velocity: int, op_id: str) -> int:
        """Read live position and reject if at/beyond the boundary."""
        _min_pos, _max_pos = self._resolve_position_limits()
        current_position = await self.get_position_live()

        if velocity > 0 and current_position >= _max_pos:
            raise RuntimeError(f"Cannot jog: at maximum position {_max_pos} (current: {current_position})")
        if velocity < 0 and current_position <= _min_pos:
            raise RuntimeError(f"Cannot jog: at minimum position {_min_pos} (current: {current_position})")

        return current_position

    def _validate_position_limits(self, target_position: int, op_id: str) -> None:
        """Reject target_position outside configured software position limits."""
        _min_pos, _max_pos = self._resolve_position_limits()

        if target_position < _min_pos:
            raise ValueError(
                f"move_to_position[{op_id}]: target_position {target_position} "
                f"below min_position_limit {_min_pos}"
            )
        if target_position > _max_pos:
            raise ValueError(
                f"move_to_position[{op_id}]: target_position {target_position} "
                f"above max_position_limit {_max_pos}"
            )

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
        """Move to target position using Profile Position mode."""
        op_id = op_id or uuid.uuid4().hex[:8]
        _LOGGER.info(
            "move_to_position[%s]: start target=%s vel=%s acc=%s dec=%s timeout_s=%.2f",
            op_id, target_position, velocity, accel, decel, float(timeout_s),
        )

        status = await self._prepare_motion_context(op_id)
        status = await self._ensure_mode_pp(status, op_id)
        await self._require_remote(status, op_id=op_id)

        self._validate_motion_params(
            velocity=velocity, accel=accel, decel=decel, timeout_s=timeout_s,
        )
        self._validate_position_limits(target_position, op_id)

        halt_cleared = cw_clear_bits(cw_enable_operation(), CWBit.HALT)
        await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cleared) & 0xFFFF, 0)
        await asyncio.sleep(self._cfg.mode_settle_delay_s)
        _LOGGER.debug("move_to_position[%s]: HALT bit cleared before PP command", op_id)

        pp = self._require_pp()

        # Diagnostic: log actual position before move
        try:
            pos_before = await self.get_position_live()
            _LOGGER.info("move_to_position[%s]: position_before=%d, target=%d", op_id, pos_before, target_position)
        except Exception:
            _LOGGER.debug("move_to_position[%s]: could not read position before move", op_id)

        motion_token = uuid.uuid4().hex
        self._abort_token = motion_token
        self._abort_event.clear()

        if require_homing:
            is_homed = await self.is_homed()
            if not is_homed:
                _LOGGER.warning(
                    "move_to_position[%s]: homing not completed — movement may be unreliable",
                    op_id,
                )

        try:
            await pp.move_to_position(
                target_position=target_position,
                profile_velocity=velocity,
                profile_accel=accel,
                profile_decel=decel,
                timeout_s=timeout_s,
            )
            if self._abort_token != motion_token:
                _LOGGER.warning(
                    "move_to_position[%s]: abort token mismatch after completion "
                    "(stop() was called during motion)",
                    op_id,
                )
            _LOGGER.info("move_to_position[%s]: completed target=%s", op_id, target_position)
        finally:
            self._schedule_idle_shutdown()

    # ---- homing ----

    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> HomingResult:
        """Perform homing operation."""
        from ..motion.homing import HomingResult

        op_id = op_id or uuid.uuid4().hex[:8]
        _LOGGER.info("home[%s]: requested timeout_s=%.2f", op_id, float(timeout_s))

        if not self.is_connected:
            raise RuntimeError("Not connected")

        status = await self._check_drive_ready(op_id=op_id)
        await self._require_remote(status, op_id=op_id)

        homing = self._require_homing()
        self._abort_event.clear()
        result = await homing.run(timeout_s=timeout_s)
        _LOGGER.info("home[%s]: completed", op_id)
        return result

    # ---- jog facade ----

    _MODE_PV = 3

    def is_jog_active(self) -> bool:
        """Return True if jog controller is currently active."""
        return self._jog is not None and self._jog.state.active

    async def is_jog_warm(self) -> bool:
        """Check if drive is already in PV mode + OPERATION_ENABLED (warm start ok)."""
        try:
            status = await self.get_status()
            if not status.get("operation_enabled", False) or status.get("fault", False):
                return False
            mode = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            return mode == self._MODE_PV
        except Exception:
            return False

    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        """Start jogging with given velocity.

        Three paths:
        - **Hot**: jog already active → update velocity+TTL (~5ms)
        - **Warm**: drive in PV+OPERATION_ENABLED → skip heavy init (~50ms)
        - **Cold**: full init with mode switch + sleeps (~500ms)
        """
        op_id = op_id or uuid.uuid4().hex[:8]
        self._cancel_idle_shutdown_timer()
        self._abort_event.clear()

        jog = self._require_jog()

        self._validate_velocity(velocity)
        if ttl_ms is not None:
            if not (50 <= ttl_ms <= 5000):
                raise ValueError(f"ttl_ms must be in range [50, 5000], got {ttl_ms}")

        await self._check_jog_boundary(velocity, op_id)
        ttl_s = ttl_ms / 1000.0 if ttl_ms is not None else None

        # Hot path: jog already active — just update velocity+TTL
        if jog.state.active:
            _LOGGER.info("jog_start[%s]: hot path (active), velocity=%s", op_id, velocity)
            await jog.press(velocity=velocity, ttl_s=ttl_s)
            return

        # Warm path: drive already in PV + OPERATION_ENABLED — skip heavy init
        if await self.is_jog_warm():
            _LOGGER.info("jog_start[%s]: warm path (PV+enabled), velocity=%s", op_id, velocity)
            await jog.press(velocity=velocity, ttl_s=ttl_s)
            return

        # Cold path: full initialization (mode may have changed since last jog)
        _LOGGER.info("jog_start[%s]: cold path, velocity=%s ttl_ms=%s", op_id, velocity, ttl_ms)
        await jog.invalidate_mode()  # force ensure_mode in next press()
        pv = self._require_pv()

        status = await self._check_drive_ready(op_id=op_id)

        halt_cleared = cw_clear_bits(cw_enable_operation(), CWBit.HALT)
        await self.write_u16(int(ODIndex.CONTROLWORD), int(halt_cleared) & 0xFFFF, 0)
        await asyncio.sleep(self._cfg.mode_settle_delay_s)

        await self._require_remote(status, op_id=op_id)

        await pv.stop_velocity_zero()
        await asyncio.sleep(self._cfg.mode_settle_delay_s)

        await jog.press(velocity=velocity, ttl_s=ttl_s)
        _LOGGER.info("jog_start[%s]: command accepted velocity=%s ttl_s=%s", op_id, velocity, ttl_s)

    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        """Update jog velocity and refresh TTL keepalive."""
        jog = self._require_jog()
        op_id = op_id or uuid.uuid4().hex[:8]

        if not jog.state.active:
            return

        try:
            await self._check_jog_boundary(velocity, op_id)
        except RuntimeError:
            await self.jog_stop()
            return

        ttl_s = ttl_ms / 1000.0 if ttl_ms is not None else None
        await jog.keepalive(velocity=velocity, ttl_s=ttl_s)

    async def jog_stop(self, *, op_id: str | None = None) -> None:
        """Stop jogging."""
        op_id = op_id or uuid.uuid4().hex[:8]
        jog = self._require_jog()

        if not jog.state.active:
            _LOGGER.debug("jog_stop[%s]: already inactive, skipping", op_id)
            return

        await jog.release()
        _LOGGER.info("jog_stop[%s]: release sent", op_id)

        self._schedule_idle_shutdown()
        _LOGGER.debug("jog_stop[%s]: shutdown timer scheduled", op_id)
