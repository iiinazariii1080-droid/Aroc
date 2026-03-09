
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared_config.network import get_service_url
import aiohttp
import asyncio
import argparse
import json
import threading
from concurrent.futures import Future
from typing import Optional, Dict, Any
from app.decorator import*
from exceptions import DeviceConnectionError, DeviceError
motor_lock = asyncio.Lock()

class IgusMotorClient:
    def __init__(self, base_url: Optional[str] = None, timeout_seconds: int = 10, operation_timeout_seconds: Optional[float] = None, motion_timeout_seconds: Optional[float] = None):
        if base_url is None:
            try:
                from core.connection_config import web_server_ip, web_server_port  # type: ignore
                base_url = f"http://{web_server_ip}:{web_server_port}"
            except Exception:
                base_url = get_service_url("igus")

        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)
        self._operation_timeout_seconds = operation_timeout_seconds
        self._motion_timeout_seconds = motion_timeout_seconds
        
    # Synchronous context manager for "with IgusMotorClient(...) as client:"
    def __enter__(self) -> "IgusMotorClient":
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

        self._thread = threading.Thread(target=runner, name="IgusMotorClientLoop", daemon=True)
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
                    # Extract concise message
                    msg: Any = data
                    if isinstance(data, dict) and "detail" in data:
                        detail = data.get("detail")
                        if isinstance(detail, dict) and "error" in detail:
                            msg = detail.get("error")
                        else:
                            msg = detail
                    elif isinstance(data, dict) and "error" in data:
                        msg = data.get("error")

                    text = f"IGUS: {msg}"
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
                    # Extract concise message
                    msg: Any = data
                    if isinstance(data, dict) and "detail" in data:
                        detail = data.get("detail")
                        if isinstance(detail, dict) and "error" in detail:
                            msg = detail.get("error")
                        else:
                            msg = detail
                    elif isinstance(data, dict) and "error" in data:
                        msg = data.get("error")

                    text = f"IGUS: {msg}"
                    if resp.status in (502, 503, 504):
                        raise DeviceConnectionError(text)
                    raise DeviceError(text)
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    @guarded_async_call(motor_lock)
    async def move(self, *, position: float, velocity_percent: float = 100.0, acceleration_percent: float = 100.0) -> Dict[str, Any]:
        payload = {"position": position, "velocity_percent": velocity_percent, "acceleration_percent": acceleration_percent}
        return await self._post("/move", json=payload, timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @guarded_async_call(motor_lock)
    async def reference(self) -> Dict[str, Any]:
        return await self._post("/reference")

    @guarded_async_call(motor_lock)
    async def fault_reset(self) -> Dict[str, Any]:
        return await self._post("/fault_reset")

    @safe_call
    async def position(self) -> Dict[str, Any]:
        return await self._get("/position")

    @safe_call
    async def is_motion(self) -> Dict[str, Any]:
        return await self._get("/is_motion")

    @safe_call
    async def status(self) -> Dict[str, Any]:
        return await self._get("/status")


async def main():
    url = get_service_url("igus")

    status = None
    with IgusMotorClient(base_url=url) as client:
        task = client.status()
        result = await task

if __name__ == "__main__":
    asyncio.run(main())