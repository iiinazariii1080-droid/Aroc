import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared_config.network import get_service_url
import aiohttp
import asyncio
import logging
import threading
from typing import Optional, Dict, Any

_log = logging.getLogger(__name__)
from app.decorator import*
from exceptions import DeviceConnectionError, DeviceError
from models.api_types import SymovoStatusResponse, ErrorStatus

symovo_lock = asyncio.Lock()


def _normalize_symovo_status(raw):
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    try:
        if not isinstance(raw, dict):
            raise ValueError("Expected dict or list of dicts")

        pose_in = raw.get("pose", {}) if isinstance(raw.get("pose"), dict) else {}
        vel_in = raw.get("velocity", {}) if isinstance(raw.get("velocity"), dict) else {}

        # Accept both old (x/y/theta) and new (x_m/y_m/theta_deg) shapes
        # Convert theta_rad to theta_deg if needed
        theta_deg = pose_in.get("theta_deg", pose_in.get("theta"))
        theta_rad = pose_in.get("theta_rad")
        if theta_deg is None and theta_rad is not None:
            import math
            theta_deg = math.degrees(theta_rad)
        elif theta_deg is None:
            theta_deg = 0.0
            
        pose_out = {
            "x_m": pose_in.get("x_m", pose_in.get("x")),
            "y_m": pose_in.get("y_m", pose_in.get("y")),
            "theta_deg": theta_deg,
            "map_id": pose_in.get("map_id"),
        }

        # Handle velocity fields, ensuring omega_rad_s is never None
        omega_rad_s = vel_in.get("omega_rad_s", vel_in.get("theta"))
        if omega_rad_s is None:
            omega_rad_s = 0.0
            
        vel_out = {
            "vx_m_s": vel_in.get("vx_m_s", vel_in.get("x")) or 0.0,
            "vy_m_s": vel_in.get("vy_m_s", vel_in.get("y")) or 0.0,
            "omega_rad_s": omega_rad_s,
        }

        battery_level_percent = None
        if "battery_level_percent" in raw:
            battery_level_percent = raw.get("battery_level_percent")
        elif isinstance(raw.get("battery_level"), (int, float)):
            battery = raw.get("battery_level")
            battery_level_percent = battery * 100.0 if 0.0 <= battery <= 1.0 else battery

        normalized = {
            "online": bool(raw.get("online", True)),
            "last_update_time": raw.get("last_update_time"),
            "id": str(raw.get("id")) if raw.get("id") is not None else None,
            "name": raw.get("name"),
            "pose": pose_out,
            "velocity": vel_out,
            "state": raw.get("state"),
            "battery_level_percent": battery_level_percent,
            "state_flags": raw.get("state_flags"),
            "robot_ip": raw.get("robot_ip") or raw.get("ip"),
            "replication_port": raw.get("replication_port"),
            "api_port": raw.get("api_port"),
            "iot_port": raw.get("iot_port"),
            "last_seen": raw.get("last_seen"),
            "enabled": raw.get("enabled"),
            "last_update_epoch": raw.get("last_update_epoch") or raw.get("last_update"),
            "attributes": raw.get("attributes"),
            "planned_path_edges": raw.get("planned_path_edges"),
        }
        return SymovoStatusResponse(**normalized)
    except Exception as e:
        return ErrorStatus(error={"type": "InvalidSymovoStatus", "msg": str(e), "raw": raw})


class SymovoAgvClient:
    def __init__(self, base_url: Optional[str] = None, timeout_seconds: int = 10, operation_timeout_seconds: Optional[float] = None, motion_timeout_seconds: Optional[float] = None):
        if base_url is None:
            base_url = get_service_url("symovo")

        self.base_url = base_url.rstrip("/")
        _log.info("SymovoAgvClient base_url=%s", self.base_url)
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)
        self._operation_timeout_seconds = operation_timeout_seconds
        self._motion_timeout_seconds = motion_timeout_seconds

    def __enter__(self) -> "SymovoAgvClient":
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

        self._thread = threading.Thread(target=runner, name="SymovoAgvClientLoop", daemon=True)
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

    async def _post(self, path: str, json: Optional[Dict[str, Any]] = None, timeout: Optional[aiohttp.ClientTimeout] = None, op_timeout: Optional[float] = None) -> Dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"

        async def do() -> Dict[str, Any]:
            async with session.post(url, json=json, timeout=timeout or self._timeout) as resp:
                data = await resp.json()
                if resp.status != 200:
                    msg: Any = data
                    if isinstance(data, dict) and "detail" in data:
                        detail = data.get("detail")
                        if isinstance(detail, dict) and "error" in detail:
                            msg = detail.get("error")
                        else:
                            msg = detail
                    elif isinstance(data, dict) and "error" in data:
                        msg = data.get("error")

                    text = f"SYMOVO: {msg}"
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
                data = await resp.json()
                if resp.status != 200:
                    msg: Any = data
                    if isinstance(data, dict) and "detail" in data:
                        detail = data.get("detail")
                        if isinstance(detail, dict) and "error" in detail:
                            msg = detail.get("error")
                        else:
                            msg = detail
                    elif isinstance(data, dict) and "error" in data:
                        msg = data.get("error")

                    text = f"SYMOVO: {msg}"
                    if resp.status in (502, 503, 504):
                        raise DeviceConnectionError(text)
                    raise DeviceError(text)
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    @safe_call
    async def status(self) -> Dict[str, Any]:
        return await self._get("/status")

    @safe_call
    async def pose(self) -> Dict[str, Any]:
        return await self._get("/pose")

    @safe_call
    async def healthz(self) -> Dict[str, Any]:
        return await self._get("/healthz")

    @safe_call
    async def map(self) -> Dict[str, Any]:
        return await self._get("/map")

    @safe_call
    async def get_map_png(self, map_id: int) -> bytes:
        """Get map as PNG image"""
        session = await self._ensure_session()
        url = f"{self.base_url}/map/{map_id}/full.png"
        
        async with session.get(url, timeout=self._timeout) as resp:
            if resp.status != 200:
                data = await resp.json()
                msg: Any = data
                if isinstance(data, dict) and "detail" in data:
                    detail = data.get("detail")
                    if isinstance(detail, dict) and "error" in detail:
                        msg = detail.get("error")
                    else:
                        msg = detail
                elif isinstance(data, dict) and "error" in data:
                    msg = data.get("error")

                text = f"SYMOVO: {msg}"
                if resp.status in (502, 503, 504):
                    raise DeviceConnectionError(text)
                raise DeviceError(text)
            return await resp.read()

    @safe_call
    async def get_transport(self, transport_id: int) -> Dict[str, Any]:
        return await self._get(f"/transport/{transport_id}")

    @guarded_async_call(symovo_lock)
    async def wait_transport_changes(self, transport_id: int) -> Dict[str, Any]:
        return await self._get(f"/transport/{transport_id}/wait_for_changes", timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @safe_call
    async def get_charging_stations(self) -> Dict[str, Any]:
        return await self._get("/charging_stations")

    @guarded_async_call(symovo_lock)
    async def go_to_charging_station(self, station_id: int) -> Dict[str, Any]:
        return await self._post(f"/go_to_charging_station/{station_id}", timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @guarded_async_call(symovo_lock)
    async def disable_all_charging_stations(self) -> Dict[str, Any]:
        return await self._post("/charging_stations/disable_all")

    @guarded_async_call(symovo_lock)
    async def fault_reset(self) -> Dict[str, Any]:
        return await self._post("/fault_reset")

    @guarded_async_call(symovo_lock)
    async def go_to_pose(self, *, x_m: float, y_m: float, theta_deg: float = 0.0, map_id: Optional[int] = None, max_speed_m_s: Optional[float] = None, wait: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "x_m": x_m,
            "y_m": y_m,
            "theta_deg": theta_deg,
            "map_id": map_id,
            "max_speed_m_s": max_speed_m_s,
            "wait": wait,
        }
        return await self._post("/go_to_pose", json=payload, timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)


async def main():
    with SymovoAgvClient() as client:
        result = await client.status()
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
