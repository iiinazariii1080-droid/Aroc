"""Status and telemetry query mixin for DryveD1.

Provides cached-or-live status reads, position, velocity, CiA402 state,
is_moving detection with mode-aware logic, and position limit accessors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..od.indices import ODIndex
from ..od.statusword import decode_statusword, infer_cia402_state
from ..transport.clock import monotonic_s

if TYPE_CHECKING:
    from ..cia402.fault import FaultManager
    from ..motion.jog import JogController
    from ..telemetry.poller import TelemetryPoller
    from ..telemetry.snapshots import DriveSnapshot
    from .drive import DryveD1Config

_LOGGER = logging.getLogger(__name__)


class StatusQueriesMixin:
    """Read-only status and telemetry queries."""

    # Provided by DryveD1
    _cfg: DryveD1Config
    _telemetry_poller: TelemetryPoller | None
    _jog: JogController | None

    # Provided by OdAccessorMixin
    async def read_u16(self, index: int, subindex: int = 0) -> int: ...
    async def read_i32(self, index: int, subindex: int = 0) -> int: ...
    async def read_i8(self, index: int, subindex: int = 0) -> int: ...

    # ---- position ----

    async def get_position(self) -> int:
        """Get current position (cached or live)."""
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.position is not None:
                return snapshot.position
        return await self.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE))

    async def get_position_live(self) -> int:
        """Read current position directly (bypass telemetry cache)."""
        return await self.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE))

    # ---- is_moving (mode-aware) ----

    _MODE_PROFILE_POSITION = 1
    _MODE_PROFILE_VELOCITY = 3

    async def is_moving(self) -> bool:
        """Check if the drive is currently in motion.

        Mode-aware: PP checks target_reached + velocity, PV checks velocity only.
        Returns True if motion is active, False if stationary.
        """
        snapshot = None
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
        if snapshot is not None and snapshot.decoded_status is not None:
            decoded = snapshot.decoded_status
            mode_disp = snapshot.mode_display
        else:
            sw = await self.read_u16(int(ODIndex.STATUSWORD))
            decoded = decode_statusword(sw)
            mode_disp = None

        # Check jog state first (if jog controller is active, it's authoritative)
        if self._jog is not None:
            jog_state = self._jog.state
            if jog_state.active:
                if monotonic_s() < jog_state.deadline_s:
                    return True

        if mode_disp is None:
            try:
                mode_disp = await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY))
            except Exception:
                return not decoded["target_reached"]

        if mode_disp == self._MODE_PROFILE_POSITION:
            return await self._is_motion_pp(decoded, snapshot)
        if mode_disp == self._MODE_PROFILE_VELOCITY:
            return await self._is_motion_pv(snapshot)
        # Mode 0 = no mode selected (idle); mode 6 = homing; others = uncommon.
        # All use target_reached as a safe fallback — only warn for truly unexpected values.
        if mode_disp not in (0, 6):
            _LOGGER.warning("is_moving: unexpected mode_display=%s, using target_reached fallback", mode_disp)
        return not decoded["target_reached"]

    async def _is_motion_pp(
        self,
        decoded: dict[str, bool],
        snapshot: DriveSnapshot | None,
    ) -> bool:
        """Profile Position mode: target_reached + velocity confirmation."""
        if decoded["target_reached"]:
            return False
        vel = await self._read_velocity_or_none(snapshot)
        if vel is None:
            return not decoded["target_reached"]
        return abs(vel) > self._cfg.velocity_threshold

    async def _is_motion_pv(self, snapshot: DriveSnapshot | None) -> bool:
        """Profile Velocity / Jog mode: velocity only."""
        vel = await self._read_velocity_or_none(snapshot)
        if vel is None:
            # Jog check already done in parent is_moving(); fail-safe: assume moving
            return True
        return abs(vel) > self._cfg.velocity_threshold

    async def _read_velocity_or_none(
        self,
        snapshot: DriveSnapshot | None,
    ) -> int | None:
        """Return cached or live velocity, or None on read failure."""
        if snapshot is not None and snapshot.velocity is not None:
            return snapshot.velocity
        try:
            return await self.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE))
        except Exception:
            return None

    # ---- status / statusword ----

    async def get_status(self) -> dict[str, bool]:
        """Get decoded statusword (cached or live)."""
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.decoded_status is not None:
                return snapshot.decoded_status
        sw = await self.read_u16(int(ODIndex.STATUSWORD))
        return decode_statusword(sw)

    async def get_status_live(self) -> dict[str, bool]:
        """Get decoded statusword from a direct OD read (bypass cache)."""
        sw = await self.read_u16(int(ODIndex.STATUSWORD))
        return decode_statusword(sw)

    async def get_statusword(self) -> int:
        """Return raw statusword (0x6041)."""
        return await self.read_u16(int(ODIndex.STATUSWORD))

    async def get_cia402_state(self):
        """Read statusword and return the inferred CiA 402 state enum."""
        sw = await self.read_u16(int(ODIndex.STATUSWORD))
        return infer_cia402_state(sw)

    async def get_velocity_actual(self) -> int:
        """Return actual velocity (0x606C)."""
        return await self.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE))

    async def get_mode_display(self) -> int:
        """Return modes of operation display (0x6061)."""
        return await self.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY))

    # ---- homing status ----

    async def is_homed(self) -> bool:
        """Check if homing has been completed (reads register 0x2014)."""
        try:
            homing_status = await self.read_u16(int(ODIndex.HOMING_STATUS), 0)
            return bool(homing_status & 0x01)
        except (TimeoutError, ConnectionError, OSError):
            raise
        except Exception:
            _LOGGER.debug("is_homed: HOMING_STATUS register read failed, assuming not homed", exc_info=True)
            return False

    # ---- fault diagnostics ----

    async def read_fault_info(self, *, include_history: bool = True) -> dict:
        """Read detailed fault diagnostics."""
        from ..cia402.fault import FaultManager

        fm = FaultManager(self)
        info = await fm.read_fault_info(include_history=include_history)
        return info.as_dict()

    # ---- position limits ----

    async def set_position_limits(self, min_position: int, max_position: int) -> None:
        """Set software position limits in the drive (0x607B / 0x607D)."""
        if not self.is_connected:  # type: ignore[attr-defined]
            raise RuntimeError("Not connected")
        if min_position >= max_position:
            raise ValueError(
                f"min_position ({min_position}) must be less than max_position ({max_position})"
            )
        await self.write_i32(int(ODIndex.MIN_POSITION_LIMIT), int(min_position), 0)  # type: ignore[attr-defined]
        await self.write_i32(int(ODIndex.MAX_POSITION_LIMIT), int(max_position), 0)  # type: ignore[attr-defined]

    async def get_position_limits(self) -> tuple[int, int]:
        """Get current software position limits from the drive."""
        if not self.is_connected:  # type: ignore[attr-defined]
            raise RuntimeError("Not connected")
        min_pos = await self.read_i32(int(ODIndex.MIN_POSITION_LIMIT), 0)
        max_pos = await self.read_i32(int(ODIndex.MAX_POSITION_LIMIT), 0)
        return (min_pos, max_pos)

    def _resolve_position_limits(self) -> tuple[int, int]:
        """Return (min_pos, max_pos) from config, with defaults 0 / 120000."""
        limits = self._cfg.drive.limits
        min_pos = limits.min_position_limit if limits.min_position_limit is not None else 0
        max_pos = limits.max_position_limit if limits.max_position_limit is not None else 120000
        return min_pos, max_pos
