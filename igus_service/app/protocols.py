"""Typed protocols for dependency injection boundaries.

These protocols define the contracts between the application layer and
driver/infrastructure adapters, enabling static type checking without
coupling to concrete implementations.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.events import EventBus
    from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State
    from drivers.dryve_d1.telemetry.snapshots import DriveSnapshot


@runtime_checkable
class DriveProtocol(Protocol):
    """Minimal contract for the motor-drive adapter used by the app layer."""

    is_connected: bool

    # -- telemetry / state ---------------------------------------------------

    async def get_status(self) -> dict[str, Any]: ...
    async def get_status_live(self) -> dict[str, Any]: ...
    async def get_position(self) -> int: ...
    async def is_moving(self) -> bool: ...
    async def is_homed(self) -> bool: ...
    async def read_u16(self, index: int, sub: int) -> int: ...
    async def read_i32(self, index: int, sub: int) -> int: ...
    async def read_i8(self, index: int, sub: int) -> int: ...
    def telemetry_latest(self) -> DriveSnapshot | None: ...
    def telemetry_poll_info(self) -> dict[str, Any]: ...
    async def get_statusword(self) -> int: ...
    async def get_cia402_state(self) -> DriverCiA402State: ...
    async def get_velocity_actual(self) -> int: ...
    async def get_mode_display(self) -> int: ...

    # -- motion commands -----------------------------------------------------

    async def jog_stop(self, *, op_id: str | None = None) -> None: ...
    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None: ...
    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None: ...
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
    ) -> None: ...
    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> Any: ...
    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None: ...
    async def quick_stop(self, *, op_id: str | None = None) -> None: ...
    async def stop(self, *, op_id: str | None = None) -> None: ...

    # -- jog state -----------------------------------------------------------

    def is_jog_active(self) -> bool: ...
    async def is_jog_warm(self) -> bool: ...

    # -- diagnostics ---------------------------------------------------------

    async def read_fault_info(self, *, include_history: bool = True) -> dict[str, Any]: ...


@runtime_checkable
class AppStateProtocol(Protocol):
    """Typed view of all attributes written to ``app.state`` during startup.

    NOTE: FastAPI's ``app.state`` is a Starlette ``State`` object that does NOT
    satisfy structural Protocol checks at runtime (it uses ``__setattr__`` /
    ``__getattr__`` internally).  This protocol exists solely for **static**
    type-checking (mypy / pyright) and IDE autocomplete — callers should NOT
    use ``isinstance(app.state, AppStateProtocol)`` at runtime.

    The app layer accesses attributes via ``getattr(state, "attr", default)``
    as a defensive pattern because Starlette State is an untyped bag.
    """

    drive: DriveProtocol | None
    event_bus: EventBus | None
    motor_lock: asyncio.Lock | None
    drive_fault_active: bool
    drive_last_error: str | None  # set to str(exc) in state.py; never the raw exception
    drive_last_telemetry_monotonic: float | None  # None until first telemetry callback fires
    drive_telemetry_callback_errors_total: int
    settings: Any  # Settings dataclass (full object, not dict)
    latest_command_trace: dict[str, Any] | None
