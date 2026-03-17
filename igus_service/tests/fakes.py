"""Shared test fakes and helpers.

Extracted from conftest.py into a standalone module to avoid pytest conftest
namespace collisions (drivers/tests/conftest.py was shadowing tests/conftest.py
when resolved via ``from tests.conftest import ...``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.events import EventType
from drivers.dryve_d1.od.statusword import CiA402State, infer_cia402_state


# ---------------------------------------------------------------------------
# Fake telemetry snapshot
# ---------------------------------------------------------------------------

@dataclass
class FakeSnapshot:
    """Minimal snapshot satisfying the attribute contract used in _read_drive_state."""
    statusword: int = 0
    position: int = 100
    velocity: int = 0
    mode_display: int = 1
    decoded_status: dict[str, bool] = field(
        default_factory=lambda: {"fault": False, "operation_enabled": True, "remote": True}
    )
    ts_monotonic_s: float = field(default_factory=time.monotonic)
    cia402_state: Any = None  # set lazily on first access

    def __post_init__(self) -> None:
        if self.cia402_state is None:
            from drivers.dryve_d1.od.statusword import infer_cia402_state
            self.cia402_state = infer_cia402_state(self.statusword)


# ---------------------------------------------------------------------------
# CiA402 state → synthetic statusword mapping
# ---------------------------------------------------------------------------

_CIA402_STATUSWORDS: dict[CiA402State, int] = {
    # Bits: b0=RTSO, b1=SO, b2=OE, b3=FAULT, b5=QS, b6=SOD, b9=REMOTE
    CiA402State.NOT_READY_TO_SWITCH_ON: 0x0200,        # remote only
    CiA402State.SWITCH_ON_DISABLED:     0x0240,         # b6 + remote
    CiA402State.READY_TO_SWITCH_ON:     0x0221,         # b0 + b5 + remote
    CiA402State.SWITCHED_ON:            0x0223,         # b0 + b1 + b5 + remote
    CiA402State.OPERATION_ENABLED:      0x0227,         # b0 + b1 + b2 + b5 + remote
    CiA402State.QUICK_STOP_ACTIVE:      0x0207,         # b0 + b1 + b2 + remote (b5=0)
    CiA402State.FAULT_REACTION_ACTIVE:  0x020F,         # b0 + b1 + b2 + b3 + remote
    CiA402State.FAULT:                  0x0208,         # b3 + remote
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeDrive:
    """Minimal in-memory drive stub satisfying the contract used by routes / use-cases.

    Configurable fault scenarios:
      - ``fault_mode=True``: get_status / get_status_live return fault=True.
      - ``status_live_raises``: get_status_live() raises the given exception.
      - ``cached_snapshot``: telemetry_latest() returns this FakeSnapshot instead of None.
    """

    def __init__(
        self,
        *,
        is_connected: bool = True,
        fault_mode: bool = False,
        status_live_raises: Exception | None = None,
        cached_snapshot: FakeSnapshot | None = None,
        raise_on_move: Exception | None = None,
        raise_on_home: Exception | None = None,
        raise_on_jog_start: Exception | None = None,
        fail_count: int = 0,
        telemetry_running: bool = True,
        initial_cia402_state: CiA402State | None = None,
        initial_position: int = 100,
        min_position: int = 0,
        max_position: int = 120_000,
    ) -> None:
        self.is_connected = is_connected
        self.fault_mode = fault_mode
        self.status_live_raises = status_live_raises
        self.cached_snapshot = cached_snapshot
        self.calls: list[tuple[str, dict | None]] = []
        self.raise_on_move = raise_on_move
        self.raise_on_home = raise_on_home
        self.raise_on_jog_start = raise_on_jog_start
        self._fail_count = fail_count
        self.telemetry_running = telemetry_running

        # CiA402 state tracking (T-01)
        if initial_cia402_state is not None:
            self._cia402_state = initial_cia402_state
        elif fault_mode:
            self._cia402_state = CiA402State.FAULT
        else:
            self._cia402_state = CiA402State.OPERATION_ENABLED

        # Position & motion tracking (T-02)
        self._position: int = initial_position
        self._is_moving: bool = False
        self._min_position: int = min_position
        self._max_position: int = max_position

        # Jog lifecycle tracking (T-04)
        self._jog_active: bool = False

    # -- status / telemetry --------------------------------------------------

    async def get_status(self) -> dict[str, bool]:
        is_fault = self._cia402_state in (CiA402State.FAULT, CiA402State.FAULT_REACTION_ACTIVE)
        is_op_enabled = self._cia402_state == CiA402State.OPERATION_ENABLED
        return {"fault": is_fault, "operation_enabled": is_op_enabled, "remote": True}

    async def get_position(self) -> int:
        return self._position

    async def is_moving(self) -> bool:
        return self._is_moving

    async def is_homed(self) -> bool:
        return True

    async def read_u16(self, index: Any, sub: Any) -> int:
        if self._fail_count > 0:
            self._fail_count -= 1
            raise OSError("Simulated Modbus failure")
        return 0

    async def read_i32(self, index: Any, sub: Any) -> int:
        return 0

    async def read_i8(self, index: Any, sub: Any) -> int:
        return 1

    async def get_status_live(self) -> dict[str, bool]:
        if self.status_live_raises is not None:
            raise self.status_live_raises
        return await self.get_status()

    def telemetry_latest(self) -> FakeSnapshot | None:
        return self.cached_snapshot

    def telemetry_poll_info(self) -> dict:
        return {"is_running": self.telemetry_running, "interval_s": 0.5}

    async def get_statusword(self) -> int:
        return _CIA402_STATUSWORDS.get(self._cia402_state, 0x0200)

    async def get_cia402_state(self) -> CiA402State:
        return self._cia402_state

    async def get_velocity_actual(self) -> int:
        return 0

    async def get_mode_display(self) -> int:
        return 1

    # -- internal helpers -----------------------------------------------------

    def _check_ready(self) -> None:
        """Reject motion commands when drive is in FAULT state (mirrors _check_drive_ready)."""
        if self._cia402_state in (CiA402State.FAULT, CiA402State.FAULT_REACTION_ACTIVE):
            raise RuntimeError("Drive is in FAULT state. Call fault_reset() first.")

    # -- motion commands (record calls, explicit signatures) -----------------

    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        self._check_ready()
        if velocity == 0:
            raise ValueError("jog_start: velocity must not be zero")
        self.calls.append(("jog_start", {"velocity": velocity, "ttl_ms": ttl_ms, "op_id": op_id}))
        if self.raise_on_jog_start is not None:
            raise self.raise_on_jog_start
        self._jog_active = True
        self._is_moving = True

    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        if not self._jog_active:
            return
        self.calls.append(("jog_update", {"velocity": velocity, "ttl_ms": ttl_ms, "op_id": op_id}))

    async def jog_stop(self, *, op_id: str | None = None) -> None:
        self.calls.append(("jog_stop", {"op_id": op_id}))
        self._jog_active = False
        self._is_moving = False

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
        self._check_ready()
        # Parameter validation matching real driver (T-03)
        if velocity <= 0:
            raise ValueError(f"move_to_position: velocity must be > 0, got {velocity}")
        if accel <= 0:
            raise ValueError(f"move_to_position: accel must be > 0, got {accel}")
        if decel <= 0:
            raise ValueError(f"move_to_position: decel must be > 0, got {decel}")
        if timeout_s <= 0:
            raise ValueError(f"move_to_position: timeout_s must be > 0, got {timeout_s}")
        if target_position < self._min_position or target_position > self._max_position:
            raise ValueError(
                f"move_to_position: target_position {target_position} "
                f"outside [{self._min_position}, {self._max_position}]"
            )
        self.calls.append(("move_to_position", {
            "target_position": target_position,
            "velocity": velocity,
            "accel": accel,
            "decel": decel,
            "timeout_s": timeout_s,
            "require_homing": require_homing,
            "op_id": op_id,
        }))
        if self.raise_on_move is not None:
            raise self.raise_on_move
        self._is_moving = True
        self._position = target_position

    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> Any:
        self.calls.append(("home", {"timeout_s": timeout_s, "op_id": op_id}))
        if self.raise_on_home is not None:
            raise self.raise_on_home
        from drivers.dryve_d1.motion.homing import HomingResult
        return HomingResult(attained=True, error=False, statusword=0x0627)

    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None:
        self.calls.append(("fault_reset", {"recover": recover, "op_id": op_id}))
        if self._cia402_state in (CiA402State.FAULT, CiA402State.FAULT_REACTION_ACTIVE):
            self.fault_mode = False
            if recover:
                self._cia402_state = CiA402State.OPERATION_ENABLED
            else:
                self._cia402_state = CiA402State.SWITCH_ON_DISABLED

    async def quick_stop(self, *, op_id: str | None = None) -> None:
        self.calls.append(("quick_stop", {"op_id": op_id}))
        self._is_moving = False

    async def stop(self, *, op_id: str | None = None) -> None:
        self.calls.append(("stop", {"op_id": op_id}))
        self._is_moving = False

    # -- jog state -----------------------------------------------------------

    def is_jog_active(self) -> bool:
        return self._jog_active

    async def is_jog_warm(self) -> bool:
        return False

    # -- diagnostics ---------------------------------------------------------

    async def read_fault_info(self, *, include_history: bool = True) -> dict[str, Any]:
        return {"statusword": None, "error_code": None, "error_register": None, "history": None}


# TEST-01: Verify FakeDrive satisfies DriveProtocol at import time.
from app.protocols import DriveProtocol as _DriveProtocol
assert isinstance(FakeDrive(), _DriveProtocol), (
    "FakeDrive does not satisfy DriveProtocol — "
    "update FakeDrive to match the protocol"
)


class AsyncNoopLock:
    """No-op async context-manager that satisfies ``async with motor_lock``.

    Also supports ``acquire()`` / ``release()`` for the ``asyncio.wait_for``
    try-acquire pattern used by ``_non_queuing_lock``.
    """

    def locked(self) -> bool:
        return False

    async def acquire(self) -> bool:
        return True

    def release(self) -> None:
        pass

    async def __aenter__(self) -> AsyncNoopLock:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class ControllableLock:
    """asyncio.Lock stand-in whose locked() return value is configurable.

    When ``locked_state=True``, ``acquire()`` never completes (simulates
    a held lock that will cause ``asyncio.wait_for(..., timeout=0)`` to
    raise ``TimeoutError``).
    """

    def __init__(self, *, locked_state: bool = False) -> None:
        self._locked_state = locked_state

    def locked(self) -> bool:
        return self._locked_state

    async def acquire(self) -> bool:
        if self._locked_state:
            # Simulate a lock that never becomes available
            import asyncio
            await asyncio.sleep(999999)
        return True

    def release(self) -> None:
        pass

    async def __aenter__(self) -> ControllableLock:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakeEventBus:
    """In-memory event bus that records publications for assertion."""

    def __init__(self) -> None:
        self.published: list[tuple[EventType, dict]] = []

    def publish(self, event_type: EventType, payload: dict) -> None:
        self.published.append((event_type, payload))


# ---------------------------------------------------------------------------
# Default app-state settings
# ---------------------------------------------------------------------------

_MISSING: object = object()

from app.config import Settings

DEFAULT_SETTINGS = Settings(
    dryve_host="127.0.0.1",
    dryve_port=502,
    dryve_unit_id=1,
    dryve_telemetry_poll_s=0.5,
    dryve_health_weight_disconnected=50,
    dryve_health_weight_startup_error=30,
    dryve_health_weight_telemetry_stale=20,
    dryve_health_weight_fault_active=30,
    dryve_health_weight_callback_error_max=20,
)


def set_app_state(
    app: Any,
    *,
    drive: Any = _MISSING,
    event_bus: FakeEventBus | None = None,
    motor_lock: AsyncNoopLock | None = None,
    settings: Any = None,
) -> FakeDrive:
    """Populate ``app.state`` with sensible defaults for testing."""
    drv: FakeDrive | None = FakeDrive() if drive is _MISSING else drive
    app.state.drive = drv
    app.state.drive_last_error = None
    app.state.drive_last_telemetry_monotonic = time.monotonic()
    app.state.drive_fault_active = False
    app.state.drive_telemetry_callback_errors_total = 0
    app.state.settings = settings if settings is not None else DEFAULT_SETTINGS
    app.state.event_bus = event_bus if event_bus is not None else FakeEventBus()
    app.state.motor_lock = motor_lock if motor_lock is not None else AsyncNoopLock()

    return drv  # type: ignore[return-value]
