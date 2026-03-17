from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from ..od.indices import ODIndex
from ..od.statusword import decode_statusword, infer_cia402_state
from ..protocol.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .snapshots import DriveSnapshot

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    interval_s: float = 0.20  # 5 Hz default; increase to 0.05 for UI/joystick loops
    read_position: bool = True
    read_velocity: bool = True
    read_mode_display: bool = True
    # If True, poller will tolerate read failures and continue.
    tolerate_errors: bool = True

class TelemetryPoller:
    """Periodic SDO poller for core CiA 402 signals.

    The poller is intentionally simple:
    - reads STATUSWORD always
    - optionally reads POSITION_ACTUAL_VALUE and VELOCITY_ACTUAL_VALUE and MODE_DISPLAY
    - stores the latest snapshot
    - optional callback on each snapshot
    """

    def __init__(
        self,
        od: AsyncODAccessor,
        *,
        config: TelemetryConfig | None = None,
        on_snapshot: Callable[[DriveSnapshot], None] | None = None,
    ) -> None:
        self._od = od
        self._cfg = config or TelemetryConfig()
        self._on = on_snapshot
        self._task: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._latest: DriveSnapshot | None = None

    @property
    def latest(self) -> DriveSnapshot | None:
        return self._latest

    @property
    def interval_s(self) -> float:
        return float(self._cfg.interval_s)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_callback(self, on_snapshot: Callable[[DriveSnapshot], None] | None) -> None:
        """Update snapshot callback at runtime.

        The callback is executed in the poller's asyncio task context.
        It MUST be fast and non-blocking (schedule any heavy work).
        """
        self._on = on_snapshot

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run(), name="dryve_d1.telemetry_poller")

    async def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_evt.set()
        if self._task is None:
            raise RuntimeError("Poller task is None despite is_running=True")
        try:
            await self._task
        finally:
            self._task = None

    async def _run(self) -> None:
        interval = max(0.02, float(self._cfg.interval_s))
        while not self._stop_evt.is_set():
            t0 = monotonic_s()
            try:
                sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
                decoded = decode_statusword(sw)
                cia_state = infer_cia402_state(sw)

                pos = vel = mode = None
                if self._cfg.read_position:
                    pos = await self._od.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE), 0)
                if self._cfg.read_velocity:
                    vel = await self._od.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE), 0)
                if self._cfg.read_mode_display:
                    mode = await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)

                snap = DriveSnapshot(
                    ts_monotonic_s=t0,
                    statusword=int(sw) & 0xFFFF,
                    cia402_state=cia_state,
                    position=pos,
                    velocity=vel,
                    mode_display=mode,
                    decoded_status=decoded,
                )
                self._latest = snap
                if self._on is not None:
                    try:
                        self._on(snap)
                    except Exception:
                        _LOGGER.warning(
                            "Snapshot callback raised — ignored to keep poller alive",
                            exc_info=True,
                        )
            except Exception:
                if not self._cfg.tolerate_errors:
                    raise
            # sleep remaining time
            dt = monotonic_s() - t0
            await asyncio.sleep(max(0.0, interval - dt))
