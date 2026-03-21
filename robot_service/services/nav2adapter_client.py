"""Thin async HTTP client for nav2adapter (AGV commands via single owner).

All AGV operations MUST go through this client so that nav2adapter is the
sole authority over the Symovo AGV.  robot_service never talks to the
Symovo controller directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp

from exceptions import DeviceConnectionError, DeviceError

_log = logging.getLogger(__name__)

# Default timeouts (seconds)
_READ_TIMEOUT = 10.0
_WRITE_TIMEOUT = 15.0
_NAVIGATE_TIMEOUT = 120.0  # go_to_pose with wait=True can take minutes


class Nav2AdapterClient:
    """Proxy all AGV commands through nav2adapter HTTP API (port 7905).

    Uses a persistent ``aiohttp.ClientSession`` instead of creating one per
    request (avoids TCP socket churn).
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._robot_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def aclose(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, *, timeout: float = _READ_TIMEOUT) -> Dict[str, Any]:
        try:
            session = self._get_session()
            async with session.get(
                f"{self._base}{path}",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    detail = data.get("detail", {}) if isinstance(data, dict) else data
                    raise DeviceError(f"nav2adapter {path} HTTP {resp.status}: {detail}")
                return data
        except (DeviceError, DeviceConnectionError):
            raise
        except Exception as exc:
            raise DeviceConnectionError(f"nav2adapter unreachable ({path}): {exc}") from exc

    async def _post(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        timeout: float = _WRITE_TIMEOUT,
    ) -> Dict[str, Any]:
        try:
            session = self._get_session()
            async with session.post(
                f"{self._base}{path}",
                json=json,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    detail = data.get("detail", {}) if isinstance(data, dict) else data
                    raise DeviceError(f"nav2adapter {path} HTTP {resp.status}: {detail}")
                return data
        except (DeviceError, DeviceConnectionError):
            raise
        except Exception as exc:
            raise DeviceConnectionError(f"nav2adapter unreachable ({path}): {exc}") from exc

    async def _put(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        timeout: float = _WRITE_TIMEOUT,
    ) -> Dict[str, Any]:
        try:
            session = self._get_session()
            async with session.put(
                f"{self._base}{path}",
                json=json,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"status": resp.status, "text": await resp.text()}
                if resp.status >= 400:
                    detail = data.get("detail", {}) if isinstance(data, dict) else data
                    raise DeviceError(f"nav2adapter {path} HTTP {resp.status}: {detail}")
                return data
        except (DeviceError, DeviceConnectionError):
            raise
        except Exception as exc:
            raise DeviceConnectionError(f"nav2adapter unreachable ({path}): {exc}") from exc

    # ------------------------------------------------------------------
    # Public API (mirrors nav2adapter/routes/symovo_agv.py endpoints)
    # ------------------------------------------------------------------

    async def pose(self) -> Dict[str, Any]:
        """GET /pose — normalized AGV pose."""
        return await self._get("/pose")

    async def status(self) -> Dict[str, Any]:
        """GET /status — normalized AGV status (incl. state_flags, velocity)."""
        return await self._get("/status")

    async def fault_reset(self) -> Dict[str, Any]:
        """POST /fault_reset — clear all transports."""
        return await self._post("/fault_reset")

    async def go_to_pose(
        self,
        *,
        x_m: float,
        y_m: float,
        theta_deg: float = 0.0,
        map_id: Optional[int | str] = None,
        max_speed_m_s: Optional[float] = None,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """POST /go_to_pose — navigate AGV to target pose."""
        body: Dict[str, Any] = {
            "x_m": x_m,
            "y_m": y_m,
            "theta_deg": theta_deg,
            "wait": wait,
        }
        if map_id is not None:
            body["map_id"] = map_id
        if max_speed_m_s is not None:
            body["max_speed_m_s"] = max_speed_m_s
        # nav2adapter may wait up to 30s for scanner flag to clear before
        # accepting the command, even with wait=False.  Use a longer timeout
        # to avoid false "unreachable" errors during scanner wait.
        timeout = _NAVIGATE_TIMEOUT if wait else 45.0
        return await self._post("/go_to_pose", json=body, timeout=timeout)

    async def go_to_charging_station(self, station_id: int) -> Dict[str, Any]:
        """POST /go_to_charging_station/{station_id} — activate charging station."""
        return await self._post(f"/go_to_charging_station/{station_id}")

    async def disable_all_charging_stations(self) -> Dict[str, Any]:
        """POST /charging_stations/disable_all — deactivate all charging stations."""
        return await self._post("/charging_stations/disable_all")

    async def safety_state(self) -> Dict[str, Any]:
        """GET /safety/state — current safety lockout state."""
        return await self._get("/safety/state", timeout=3.0)

    # ------------------------------------------------------------------
    # Drive mode & teleop (route through nav2adapter, not direct Symovo)
    # ------------------------------------------------------------------

    async def drive_mode(self, *, enable: bool) -> Dict[str, Any]:
        """PUT /drive_mode — enable/disable AGV drive motors."""
        return await self._put("/drive_mode", params={"enable": str(enable).lower()})

    async def _resolve_robot_id(self) -> str:
        """Fetch robot_id from nav2adapter /api/v1/robots (cached after first call).

        DO NOT hardcode the robot_id (e.g. "default" or "fahrdummy-01").
        The aehub /api/v1/robots/{robot_id}/move/speed endpoint validates
        the id and returns 404 if it doesn't match, which silently breaks
        micro-step navigation.
        """
        if self._robot_id is None:
            data = await self._get("/api/v1/robots", timeout=5.0)
            robots = data.get("robots", [])
            if not robots:
                raise DeviceConnectionError("nav2adapter returned no robots")
            self._robot_id = robots[0]["id"]
            _log.info("Resolved robot_id: %s", self._robot_id)
        return self._robot_id

    async def teleop_move(
        self,
        *,
        speed: float,
        angular_speed: float = 0.0,
        duration: float = 0.25,
    ) -> Dict[str, Any]:
        """PUT /api/v1/robots/{robot_id}/move/speed — teleop speed command.

        Routes through nav2adapter's aehub endpoint so the state store
        is aware of teleop motion.
        """
        rid = await self._resolve_robot_id()
        body = {
            "speed": speed,
            "angular_speed": angular_speed,
            "duration": duration,
        }
        return await self._put(
            f"/api/v1/robots/{rid}/move/speed",
            json=body,
            timeout=duration + 3.0,
        )

    async def navigation_status(self) -> Dict[str, Any]:
        """GET /api/v1/robots/{robot_id}/status/navigation — current navigation status.

        Returns dict with keys: status (idle|navigating|arrived|error),
        goal_id, progress_percent, error_reason.
        """
        rid = await self._resolve_robot_id()
        return await self._get(f"/api/v1/robots/{rid}/status/navigation", timeout=5.0)

    async def teleop_config_get(self) -> Dict[str, Any]:
        """GET /teleop/config — current AGV teleop parameters."""
        return await self._get("/teleop/config", timeout=5.0)

    async def teleop_config_put(self, *, json: Dict[str, Any]) -> Dict[str, Any]:
        """PUT /teleop/config — update AGV teleop parameters (partial)."""
        return await self._put("/teleop/config", json=json, timeout=5.0)
