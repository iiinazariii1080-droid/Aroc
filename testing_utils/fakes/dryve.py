"""Fake DryveD1 driver for testing igus_service without real Modbus hardware.

Implements the ``DriveProtocol`` from ``igus_service.app.protocols`` so it can
be injected into the application layer as a drop-in replacement.

Usage::

    from testing_utils.fakes.dryve import FakeDryveD1

    @pytest.fixture
    def drive():
        return FakeDryveD1()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FakeDriveSnapshot:
    """Minimal telemetry snapshot stub."""

    position: int = 0
    velocity: int = 0
    statusword: int = 0x0227  # default: switched_on | voltage_enabled | quick_stop | switch_on_disabled
    fault_active: bool = False
    homed: bool = True
    moving: bool = False
    target_reached: bool = True
    timestamp: float = 0.0


class FakeDryveD1:
    """In-memory stub for dryve D1 Modbus TCP drive.

    Satisfies the ``DriveProtocol`` interface used by the igus_service
    application layer (``app.state``, ``app.routes``, ``DriveService``).
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        position: int = 0,
        homed: bool = True,
        fault: bool = False,
    ) -> None:
        self.is_connected: bool = connected
        self._position: int = position
        self._homed: bool = homed
        self._fault: bool = fault
        self._moving: bool = False
        self._velocity: int = 0
        self._registers: dict[tuple[Any, Any], int] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._snapshot = FakeDriveSnapshot(
            position=position, homed=homed, fault_active=fault,
        )

    # ── telemetry / state ───────────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        self.calls.append(("get_status", {}))
        return {
            "position": self._position,
            "velocity": self._velocity,
            "homed": self._homed,
            "moving": self._moving,
            "fault": self._fault,
            "connected": self.is_connected,
        }

    async def get_status_live(self) -> dict[str, Any]:
        return await self.get_status()

    async def get_position(self) -> int:
        return self._position

    async def is_motion(self) -> bool:
        return self._moving

    async def is_homed(self) -> bool:
        return self._homed

    async def read_u16(self, index: Any, sub: Any) -> int:
        return self._registers.get((index, sub), 0)

    async def read_i32(self, index: Any, sub: Any) -> int:
        return self._registers.get((index, sub), 0)

    async def read_i8(self, index: Any, sub: Any) -> int:
        return self._registers.get((index, sub), 0)

    def telemetry_latest(self) -> FakeDriveSnapshot:
        return self._snapshot

    # ── motion commands ─────────────────────────────────────────────────

    async def jog_stop(self, **kwargs: Any) -> None:
        self.calls.append(("jog_stop", kwargs))
        self._moving = False
        self._velocity = 0

    async def jog_start(self, **kwargs: Any) -> None:
        self.calls.append(("jog_start", kwargs))
        self._moving = True
        self._velocity = kwargs.get("velocity", 1000)

    async def jog_update(self, **kwargs: Any) -> None:
        self.calls.append(("jog_update", kwargs))

    async def move_to_position(self, **kwargs: Any) -> None:
        self.calls.append(("move_to_position", kwargs))
        target = kwargs.get("position", kwargs.get("target", self._position))
        self._position = int(target)
        self._moving = False

    async def home(self, **kwargs: Any) -> Any:
        self.calls.append(("home", kwargs))
        self._homed = True
        self._position = 0
        return {"success": True}

    async def fault_reset(self, **kwargs: Any) -> None:
        self.calls.append(("fault_reset", kwargs))
        self._fault = False

    async def quick_stop(self, **kwargs: Any) -> None:
        self.calls.append(("quick_stop", kwargs))
        self._moving = False
        self._velocity = 0

    async def stop(self, **kwargs: Any) -> None:
        self.calls.append(("stop", kwargs))
        self._moving = False
        self._velocity = 0

    # ── lifecycle ───────────────────────────────────────────────────────

    async def connect(self) -> None:
        self.calls.append(("connect", {}))
        self.is_connected = True

    async def disconnect(self) -> None:
        self.calls.append(("disconnect", {}))
        self.is_connected = False

    # ── test helpers ────────────────────────────────────────────────────

    def set_position(self, pos: int) -> None:
        self._position = pos
        self._snapshot.position = pos

    def set_fault(self, active: bool = True) -> None:
        self._fault = active
        self._snapshot.fault_active = active

    def set_moving(self, moving: bool = True) -> None:
        self._moving = moving
        self._snapshot.moving = moving

    def last_call(self) -> tuple[str, dict[str, Any]] | None:
        return self.calls[-1] if self.calls else None

    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]
