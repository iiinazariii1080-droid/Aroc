import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared_config.network import DEVICES
import aiohttp
import asyncio
import json
import threading
from typing import Optional, Dict, Any
from app.decorator import*

camera_lock = asyncio.Lock()

class CameraClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: int = 10,
        operation_timeout_seconds: Optional[float] = None,
    ):
        # Default camera API base URL
        self.base_url = (base_url or f"http://{DEVICES.DEPTH_CAMERA_IP}:8000").rstrip("/")

        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)
        self._operation_timeout_seconds = operation_timeout_seconds

    # Synchronous context manager for "with CameraClient(...) as client:"
    def __enter__(self) -> "CameraClient":
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

        self._thread = threading.Thread(target=runner, name="CameraClientLoop", daemon=True)
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

    async def _post(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"

        async def do() -> Dict[str, Any]:
            async with session.post(url, json=json, timeout=timeout or self._timeout) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"POST {path} failed: {resp.status} {data}")
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        op_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"

        async def do() -> Dict[str, Any]:
            async with session.get(url, params=params, timeout=timeout or self._timeout) as resp:
                # Depth endpoint returns JSON; index returns text/html (handled separately)
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"GET {path} failed: {resp.status} {data}")
                return data

        return await asyncio.wait_for(do(), timeout=op_timeout) if op_timeout is not None else await do()

    @guarded_async_call(camera_lock)
    async def offer(self, *, sdp: str, type: str, color_index: int = 98, stereo_index: int = 39, mode: str = "overlay") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "sdp": sdp,
            "type": type,
            "color_index": color_index,
            "stereo_index": stereo_index,
            "mode": mode,
        }
        return await self._post("/offer", json=payload, timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @guarded_async_call(camera_lock)
    async def overlay_offer(self, *, sdp: str, type: str, color_index: int = 98, stereo_index: int = 39, mode: str = "overlay") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "sdp": sdp,
            "type": type,
            "color_index": color_index,
            "stereo_index": stereo_index,
            "mode": mode,
        }
        return await self._post("/overlay_offer", json=payload, timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @safe_call
    async def depth(self, *, x_norm: float, y_norm: float) -> Dict[str, Any]:
        # API expects query param 'message' as a JSON string {"x": <0..100>, "y": <0..100>}
        # Clamp values to [0, 100]
        try:
            x_val = max(0.0, min(100.0, float(x_norm)))
        except Exception:
            x_val = 50.0
        try:
            y_val = max(0.0, min(100.0, float(y_norm)))
        except Exception:
            y_val = 50.0
        message = json.dumps({"x": x_val, "y": y_val})
        x_int = int(round(x_val))
        y_int = int(round(y_val))
        params = {"message": message, "x": x_int, "y": y_int}
        try:
            # Prefer modern JSON-style query while still sending legacy fields up-front.
            return await self._get("/depth", params=params)
        except RuntimeError as exc:
            err_txt = str(exc)
            legacy_required = "422" in err_txt and "Field required" in err_txt
            if legacy_required:
                # Depth service rejected combined query (likely legacy build) -> retry with plain ?x=&y=.
                return await self._get("/depth", params={"x": x_int, "y": y_int})
            raise

    @guarded_async_call(camera_lock)
    async def submit_polygon(self, *, points: Any, polygon: Any) -> Dict[str, Any]:
        # points: list of {id:int, name:str, u:float[0..1], v:float[0..1], d: Optional[float]}
        # polygon: list[int] (indices into points array)
        payload: Dict[str, Any] = {
            "points": points,
            "polygon": polygon,
        }
        return await self._post("/polygon", json=payload, timeout=self._infinite_timeout, op_timeout=self._operation_timeout_seconds)

    @safe_call
    async def index(self) -> Dict[str, Any]:
        # Returns raw HTML; provide simple wrapper
        session = await self._ensure_session()
        url = f"{self.base_url}/"
        async with session.get(url, timeout=self._timeout) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"GET / failed: {resp.status} {text[:200]}")
            return {"html": text}


async def main():
    # Example usage
    with CameraClient() as client:
        # Example: query center depth (0.5, 0.5)
        result = await client.depth(x_norm=0.5, y_norm=0.5)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())


