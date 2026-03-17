
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared_config.network import get_service_url
import aiohttp
import asyncio
import argparse
import json
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable, Awaitable
from models.types import MoveWithJointsDictParams, MoveWithJointsParams, MoveWithPoseParams, MoveWithToolParams
from app.decorator import*
from exceptions import DeviceConnectionError, DeviceError
xarm_lock = asyncio.Lock()


@dataclass(frozen=True)
class _ResiliencePolicy:
    max_attempts: int
    backoff_s: float
    auto_recover: bool

class XarmManipulatorClient:
    def __init__(self, base_url: Optional[str] = None, timeout_seconds: int = 10, operation_timeout_seconds: Optional[float] = None, motion_timeout_seconds: Optional[float] = None):
        if base_url is None:
            try:
                from core.connection_config import web_server_ip, web_server_port  # type: ignore
                base_url = f"http://{web_server_ip}:{web_server_port}"
            except Exception:
                base_url = get_service_url("xarm")

        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)
        self._operation_timeout_seconds = operation_timeout_seconds
        self._motion_timeout_seconds = motion_timeout_seconds
        self._motion_policy = _ResiliencePolicy(max_attempts=2, backoff_s=0.10, auto_recover=True)
        self._gripper_io_policy = _ResiliencePolicy(max_attempts=3, backoff_s=0.12, auto_recover=True)
        self._gripper_status_policy = _ResiliencePolicy(max_attempts=3, backoff_s=0.12, auto_recover=True)
        
    # Synchronous context manager for "with XarmManipulatorClient(...) as client:"
    def __enter__(self) -> "XarmManipulatorClient":
        self._ensure_loop_running()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _ensure_loop_running(self) -> None:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()

        def runner() -> None:
            asyncio.set_event_loop(self._loop)  # type: ignore[arg-type]
            self._loop.run_forever()  # type: ignore[union-attr]

        self._thread = threading.Thread(target=runner, name="XarmManipulatorClientLoop", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._loop is None:
            return
        # Close aiohttp session in its loop
        if self._session is not None:
            async def _close_session() -> None:
                if self._session is not None:
                    await self._session.close()

            fut = asyncio.run_coroutine_threadsafe(_close_session(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            self._session = None
        # Stop loop and join thread
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    @staticmethod
    def _extract_error_message(data: Any) -> Any:
        msg: Any = data
        if isinstance(data, dict) and "detail" in data:
            detail = data.get("detail")
            if isinstance(detail, dict) and "error" in detail:
                msg = detail.get("error")
            else:
                msg = detail
        elif isinstance(data, dict) and "error" in data:
            msg = data.get("error")
        return msg

    @staticmethod
    def _normalize_error_text(exc: Exception) -> str:
        return str(exc).lower()

    @staticmethod
    def _is_motion_recoverable(exc: Exception) -> bool:
        msg = XarmManipulatorClient._normalize_error_text(exc)
        markers = (
            "motion not enabled",
            "set_tool_position code=1",
            "set_servo_angle code=1",
            "state=4",
            "controllererror",
            "robot faulted",
            "recovery required",
        )
        return any(marker in msg for marker in markers)

    @staticmethod
    def _is_gripper_recoverable(exc: Exception) -> bool:
        msg = XarmManipulatorClient._normalize_error_text(exc)
        markers = (
            "controllererror",
            "code: 19",
            "c19",
            "end module communication error",
            "robot faulted",
            "recovery required",
            "status unstable",
        )
        return any(marker in msg for marker in markers)

    async def _run_with_resilience(
        self,
        op_name: str,
        request_fn: Callable[[], Awaitable[Dict[str, Any]]],
        policy: _ResiliencePolicy,
        recoverable_predicate: Callable[[Exception], bool],
    ) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return await request_fn()
            except Exception as exc:
                last_exc = exc
                is_last_attempt = attempt >= policy.max_attempts
                if is_last_attempt or not recoverable_predicate(exc):
                    raise
                if policy.auto_recover:
                    try:
                        await self._recover_and_enable_motion()
                    except Exception:
                        # keep original failure cause in retry path
                        pass
                if policy.backoff_s > 0:
                    await asyncio.sleep(policy.backoff_s * attempt)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{op_name} failed without exception")

    async def _post(self, path: str, json: Optional[Dict[str, Any]] = None, timeout: Optional[aiohttp.ClientTimeout] = None, op_timeout: Optional[float] = None) -> Dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"

        async def do() -> Dict[str, Any]:
            async with session.post(url, json=json, timeout=timeout or self._timeout) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"error": await resp.text()}
                if resp.status != 200:
                    msg = self._extract_error_message(data)

                    text = f"XARM: {msg}"
                    if resp.status in (502, 503, 504):
                        raise DeviceConnectionError(text)
                    raise DeviceError(text)
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[aiohttp.ClientTimeout] = None, op_timeout: Optional[float] = None) -> Dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"

        async def do() -> Dict[str, Any]:
            async with session.get(url, params=params, timeout=timeout or self._timeout) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"error": await resp.text()}
                if resp.status != 200:
                    msg = self._extract_error_message(data)

                    text = f"XARM: {msg}"
                    if resp.status in (502, 503, 504):
                        raise DeviceConnectionError(text)
                    raise DeviceError(text)
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    async def _recover_and_enable_motion(self) -> None:
        await self._post("/recover")
        await self._post("/enable_motion")

    @guarded_async_call(xarm_lock)
    async def complex_move_with_joints(self, params: MoveWithJointsDictParams):
        return await self._run_with_resilience(
            op_name="complex_move_with_joints",
            request_fn=lambda: self._post("/complex_move/with_joints_dict", json=params.model_dump(), timeout=self._infinite_timeout),
            policy=self._motion_policy,
            recoverable_predicate=self._is_motion_recoverable,
        )

    @guarded_async_call(xarm_lock)
    async def move_with_joints(self, params: MoveWithJointsParams):
        return await self._run_with_resilience(
            op_name="move_with_joints",
            request_fn=lambda: self._post("/move/change_joints", json=params.model_dump(), timeout=self._infinite_timeout),
            policy=self._motion_policy,
            recoverable_predicate=self._is_motion_recoverable,
        )

    @guarded_async_call(xarm_lock)
    async def move_to_pose(self, params: MoveWithPoseParams):
        return await self._run_with_resilience(
            op_name="move_to_pose",
            request_fn=lambda: self._post("/move/change_pose", json=params.model_dump(), timeout=self._infinite_timeout),
            policy=self._motion_policy,
            recoverable_predicate=self._is_motion_recoverable,
        )

    @guarded_async_call(xarm_lock)
    async def change_tool_position(self, params: MoveWithToolParams):
        return await self._run_with_resilience(
            op_name="change_tool_position",
            request_fn=lambda: self._post("/move/change_tool_position", json=params.model_dump(), timeout=self._infinite_timeout),
            policy=self._motion_policy,
            recoverable_predicate=self._is_motion_recoverable,
        )

    @safe_call
    async def gripper_drop(self) -> Dict[str, Any]:
        return await self._run_with_resilience(
            op_name="gripper_drop",
            request_fn=lambda: self._post("/gripper/drop"),
            policy=self._gripper_io_policy,
            recoverable_predicate=self._is_gripper_recoverable,
        )

    @safe_call
    async def gripper_take(self) -> Dict[str, Any]:
        return await self._run_with_resilience(
            op_name="gripper_take",
            request_fn=lambda: self._post("/gripper/take"),
            policy=self._gripper_io_policy,
            recoverable_predicate=self._is_gripper_recoverable,
        )

    @safe_call
    async def gripper_status(self) -> Dict[str, Any]:
        return await self._run_with_resilience(
            op_name="gripper_status",
            request_fn=lambda: self._get("/gripper/status"),
            policy=self._gripper_status_policy,
            recoverable_predicate=self._is_gripper_recoverable,
        )

    @safe_call
    async def fault_reset(self) -> Dict[str, Any]:
        return await self._post("/recover")

    @safe_call
    async def enable_motion(self) -> Dict[str, Any]:
        return await self._post("/enable_motion")

    @safe_call
    async def current_joints_position(self) -> Dict[str, Any]:
        return await self._get("/joints_position")

    @safe_call
    async def current_position(self) -> Dict[str, Any]:
        return await self._get("/current_position")

    @safe_call
    async def status(self) -> Dict[str, Any]:
        return await self._get("/status")

    @safe_call
    async def tcp_position(self) -> Dict[str, Any]:
        return await self._get("/tcp_position")

    @guarded_async_call(xarm_lock)
    async def set_tcp_position(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._run_with_resilience(
            op_name="set_tcp_position",
            request_fn=lambda: self._post("/move/set_tcp_position", json=params, timeout=self._infinite_timeout),
            policy=self._motion_policy,
            recoverable_predicate=self._is_motion_recoverable,
        )

    # ── Grasp analysis endpoints ───────────────────────────────────────────

    @safe_call
    async def capture_depth_frame(self) -> Dict[str, Any]:
        """Capture and cache a depth frame on xarm_service. Returns {frame_id, timestamp_ms, depth_at_center_mm}."""
        return await self._post("/depth/capture_frame")

    @safe_call
    async def analyze_grasp(
        self,
        frame_id: Optional[str] = None,
        x_norm: float = 50.0,
        y_norm: float = 50.0,
        yaw_search_step_deg: Optional[float] = None,
        collision_envelope_mm: Optional[float] = None,
        seal_perimeter_points: Optional[int] = None,
        cup_grid_step_mm: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Analyse a depth frame for optimal dual-cup grasp placement."""
        payload: Dict[str, Any] = {"target_x_norm": x_norm, "target_y_norm": y_norm}
        if frame_id is not None:
            payload["frame_id"] = frame_id
        if yaw_search_step_deg is not None:
            payload["yaw_search_step_deg"] = yaw_search_step_deg
        if collision_envelope_mm is not None:
            payload["collision_envelope_mm"] = collision_envelope_mm
        if seal_perimeter_points is not None:
            payload["seal_perimeter_points"] = seal_perimeter_points
        if cup_grid_step_mm is not None:
            payload["cup_grid_step_mm"] = cup_grid_step_mm
        return await self._post("/depth/analyze_grasp", json=payload)

async def main():
    url = get_service_url("xarm")

    status = None
    with XarmManipulatorClient(base_url=url) as client:
        params = MoveWithToolParams(
            x_offset_mm=10,
            y_offset_mm=0,
            z_offset_mm=0,
            velocity_percent=10,
            reset_faults=False
        )
        task = client.change_tool_position(params)
        result = await task
        print(result)

if __name__ == "__main__":
    asyncio.run(main())