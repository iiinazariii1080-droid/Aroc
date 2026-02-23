"""SafetyMonitor: background task that checks workspace at 20-50 Hz; STOP + fault on violation."""
import asyncio
import logging
from typing import Callable, Any, Optional

from fastapi.concurrency import run_in_threadpool
from .envelope import SafetyEnvelope
from .models import CheckResult

logger = logging.getLogger(__name__)


class SafetyMonitor:
    """
    Run at monitor_rate_hz; read current joints, FK, check envelope.
    On violation: trigger STOP, set faulted, log SAFETY_VIOLATION.
    """

    def __init__(
        self,
        get_joints: Callable[[], Any],  # sync, returns (code, angles) or angles
        get_fk: Callable[[list], Any],  # sync, returns (code, pose)
        state_store_getter: Callable[[], Any],
        envelope: Optional[SafetyEnvelope] = None,
        rate_hz: float = 30.0,
        stop_callback: Optional[Callable[[], Any]] = None,  # async, call to request STOP
    ):
        self._get_joints = get_joints
        self._get_fk = get_fk
        self._get_store = state_store_getter
        self._envelope = envelope or SafetyEnvelope()
        self._interval = 1.0 / rate_hz if rate_hz > 0 else 0.05
        self._stop_callback = stop_callback
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())
        logger.info("SafetyMonitor started at %.1f Hz", 1.0 / self._interval)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("SafetyMonitor stopped")

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._interval)
                store = self._get_store()
                if not store.connected:
                    continue
                joints_result = await run_in_threadpool(self._get_joints)
                if isinstance(joints_result, tuple):
                    code, angles = joints_result[0], joints_result[1] if len(joints_result) > 1 else None
                else:
                    code, angles = 0, joints_result
                if code != 0 or not angles or len(angles) < 6:
                    continue
                angles_7 = angles[:7] if len(angles) >= 7 else list(angles) + [0.0] * (7 - len(angles))
                fk_result = await run_in_threadpool(self._get_fk, angles_7)
                if isinstance(fk_result, tuple):
                    fk_code, pose = fk_result[0], fk_result[1] if len(fk_result) > 1 else None
                else:
                    fk_code, pose = 0, fk_result
                if fk_code != 0 or not pose or len(pose) < 3:
                    continue
                check = self._envelope.check_tcp_in_ws(pose)
                if not check.ok:
                    for v in check.violations:
                        logger.warning(
                            "SAFETY_VIOLATION link=%s x=%.1f y=%.1f z=%.1f axis=%s margin_mm=%.1f",
                            v.point_name, v.x, v.y, v.z, v.axis, v.margin_mm,
                        )
                    store.set_robot(error_code=9001)
                    if self._stop_callback:
                        await self._stop_callback()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("SafetyMonitor tick: %s", e)
