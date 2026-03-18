"""
Optimized client for Symovo AGV.
"""
import asyncio
import logging
import math
import time
import aiohttp
from typing import Optional, Dict, Any, List

from app.decorator import safe_call, guarded_async_call
from app.cache import cached, cache_invalidate
from services.base_http_client import BaseHttpClient
from services.reliability_metrics import reliability_metrics
from exceptions import DeviceConnectionError, DeviceError
from models.api_types import SymovoStatusResponse, ErrorStatus
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Exception tuple for agv/amr endpoint fallback.
_FALLBACK_EXCEPTIONS = (DeviceError, DeviceConnectionError, aiohttp.ClientError, asyncio.TimeoutError)


def normalize_symovo_status(raw):
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    try:
        if not isinstance(raw, dict):
            raw = {}

        # Symovo endpoints sometimes return:
        # - status: {"pose": {...}, "velocity": {...}, ...}
        # - pose: {"x": ..., "y": ..., "theta": ..., "map_id": ...}
        pose = raw.get("pose", {})
        if not isinstance(pose, dict) or not pose:
            # fallback for pose endpoint shape
            pose = {
                "x": raw.get("x"),
                "y": raw.get("y"),
                "theta": raw.get("theta"),
                "map_id": raw.get("map_id"),
            }

        velocity = raw.get("velocity", {})
        if not isinstance(velocity, dict):
            velocity = {}
        
        # Convert radians to degrees for theta
        theta_rad = pose.get("theta")
        theta_deg = None
        if theta_rad is not None and isinstance(theta_rad, (int, float)):
            theta_deg = math.degrees(theta_rad)
        
        # Convert angular velocity from rad/s to deg/s
        omega_rad_s = velocity.get("theta")
        omega_deg_s = None
        if omega_rad_s is not None and isinstance(omega_rad_s, (int, float)):
            omega_deg_s = math.degrees(omega_rad_s)
        
        normalized = {
            "online": True,
            "last_update_time": None,
            "id": str(raw.get("id")) if raw.get("id") is not None else None,
            "name": raw.get("name"),
            "pose": {
                "x_m": pose.get("x"),
                "y_m": pose.get("y"),
                "theta_deg": theta_deg,  # Now returning degrees
                "map_id": pose.get("map_id"),
            },
            "velocity": {
                "vx_m_s": velocity.get("x"),
                "vy_m_s": velocity.get("y"),
                "omega_deg_s": omega_deg_s,  # Now returning degrees per second
            },
            "state": raw.get("state"),
            "battery_level_percent": (raw.get("battery_level") * 100.0) if isinstance(raw.get("battery_level"), (int, float)) else None,
            "state_flags": raw.get("state_flags"),
            "robot_ip": raw.get("ip"),
            "replication_port": raw.get("replication_port"),
            "api_port": raw.get("api_port"),
            "iot_port": raw.get("iot_port"),
            "last_seen": str(raw.get("last_seen")) if raw.get("last_seen") is not None else None,
            "enabled": raw.get("enabled"),
            "last_update_epoch": raw.get("last_update"),
            "attributes": raw.get("attributes"),
            "planned_path_edges": raw.get("planned_path_edges"),
        }
        return SymovoStatusResponse(**normalized)
    except Exception as e:  # catch-all: normalisation must never crash the status pipeline
        _LOGGER.warning("Failed to normalize Symovo status: %s (raw keys: %s)", e, list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
        return ErrorStatus(error={"type": "InvalidSymovoStatus", "msg": "Failed to parse controller status"})


class SymovoAgvClient(BaseHttpClient):
    """Optimized client for Symovo AGV."""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        robot_number: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        operation_timeout_seconds: Optional[float] = None,
        motion_timeout_seconds: Optional[float] = None,
        allow_invalid_certs: Optional[bool] = None
    ):
        # Use settings from config as defaults
        base_url = base_url or settings.symovo_base_url
        robot_number = robot_number if robot_number is not None else settings.symovo_robot_number
        timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.symovo_timeout_seconds
        allow_invalid_certs = allow_invalid_certs if allow_invalid_certs is not None else settings.symovo_allow_invalid_certs
        
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            allow_invalid_certs=allow_invalid_certs
        )
        
        self.robot_number = robot_number
        self._operation_timeout_seconds = operation_timeout_seconds if operation_timeout_seconds is not None else settings.symovo_operation_timeout_seconds
        self._motion_timeout_seconds = motion_timeout_seconds if motion_timeout_seconds is not None else settings.symovo_motion_timeout_seconds
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)

        # Fine-grained locks: safety ops never block navigation, admin never blocks e-stop.
        self._safety_lock = asyncio.Lock()       # e-stop, fuse, pause
        self._navigation_lock = asyncio.Lock()   # transport create/start/stop/move
        self._admin_lock = asyncio.Lock()         # clear_all, set_drive_mode, create_new_job

    async def _with_endpoint_fallback(
        self,
        method: str,
        primary_path: str,
        fallback_path: str,
        *,
        exceptions: tuple = _FALLBACK_EXCEPTIONS,
        **kwargs,
    ) -> Any:
        """Try primary endpoint; on transient/device error, retry with fallback."""
        caller = getattr(self, method)
        try:
            return await caller(primary_path, **kwargs)
        except exceptions:
            return await caller(fallback_path, **kwargs)

    @safe_call
    @cached(ttl=1, key_prefix="symovo_pose")
    async def pose(self) -> Dict[str, Any]:
        """Get current AGV pose (cached 1s for UI/routes)."""
        return await self._pose_impl()

    @safe_call
    async def pose_uncached(self) -> Dict[str, Any]:
        """Get current AGV pose (uncached) for background loops."""
        return await self._pose_impl()

    async def _pose_impl(self) -> Dict[str, Any]:
        """Internal: fetch pose with agv/amr fallback."""
        return await self._with_endpoint_fallback(
            "get",
            f"/agv/{self.robot_number}/pose",
            f"/amr/{self.robot_number}/pose",
        )

    @safe_call
    @cached(ttl=1, key_prefix="symovo_status")
    async def status(self) -> Dict[str, Any]:
        """Get AGV status (cached 1s for UI/routes)."""
        return await self._status_impl()

    @safe_call
    async def status_uncached(self) -> Dict[str, Any]:
        """Get AGV status (uncached) to immediately see flag changes."""
        return await self._status_impl()

    async def _status_impl(self) -> Dict[str, Any]:
        """Internal: fetch status with agv/amr fallback."""
        return await self._with_endpoint_fallback(
            "get",
            f"/agv/{self.robot_number}",
            f"/amr/{self.robot_number}",
        )

    @guarded_async_call("_admin_lock", timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def set_drive_mode(self, *, enable: bool = True) -> Dict[str, Any]:
        """Switch AMR into drive mode (enable/disable motors).

        max_retries=0: controller 503 means "robot not ready" (physical state),
        retrying won't help and causes gateway timeout (504).
        """
        payload: Dict[str, Any] = {"enable": bool(enable)}
        return await self._with_endpoint_fallback(
            "put",
            f"/agv/{self.robot_number}/move/drive_mode",
            f"/amr/{self.robot_number}/move/drive_mode",
            exceptions=(DeviceError,),
            json_data=payload,
            max_retries=0,
        )

    async def move_speed(
        self,
        speed: Optional[float] = None,
        angular_speed: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send speed command for teleop (joystick/keyboard).

        Does not acquire any lock so navigation is never blocked.
        """
        effective_speed = float(speed) if speed is not None else float(settings.teleop_default_linear_speed)
        effective_angular = float(angular_speed) if angular_speed is not None else float(settings.teleop_default_angular_speed)
        effective_duration = float(duration) if duration is not None else float(settings.teleop_default_duration)
        payload: Dict[str, Any] = {
            "speed": effective_speed,
            "angular_speed": effective_angular,
            "duration": effective_duration,
        }
        op_timeout = max(1.0, effective_duration + 2.0)
        return await self._with_endpoint_fallback(
            "put",
            f"/agv/{self.robot_number}/move/speed",
            f"/amr/{self.robot_number}/move/speed",
            json_data=payload,
            op_timeout=op_timeout,
        )

    @guarded_async_call("_safety_lock", timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def pause_stop(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/pause/stop"""
        return await self._with_endpoint_fallback(
            "put",
            f"/amr/{self.robot_number}/pause/stop",
            f"/agv/{self.robot_number}/pause/stop",
        )

    @guarded_async_call("_safety_lock", timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def pause_start(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/pause/start"""
        return await self._with_endpoint_fallback(
            "put",
            f"/amr/{self.robot_number}/pause/start",
            f"/agv/{self.robot_number}/pause/start",
        )

    @guarded_async_call("_safety_lock", timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def reset_emergency_stop(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/safety/reset_emergency_stop"""
        return await self._with_endpoint_fallback(
            "put",
            f"/amr/{self.robot_number}/safety/reset_emergency_stop",
            f"/agv/{self.robot_number}/safety/reset_emergency_stop",
        )

    @guarded_async_call("_safety_lock", timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def reset_software_fuse(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/agv/{id}/software_fuse/reset

        Resets the software fuse (sfuse_blown flag) after a high motor
        current event.  The controller returns 202 on success.
        """
        return await self._with_endpoint_fallback(
            "put",
            f"/agv/{self.robot_number}/software_fuse/reset",
            f"/amr/{self.robot_number}/software_fuse/reset",
        )

    @safe_call
    @cached(ttl=30, key_prefix="symovo_job")  # Cache for 30 seconds
    async def job(self) -> Dict[str, Any]:
        """Get current job info."""
        return await self.get("/job")

    @guarded_async_call("_admin_lock")
    @cache_invalidate("symovo_job")
    async def create_new_job(self, name: str) -> Dict[str, Any]:
        """Create a new job."""
        return await self.get(
            "/new_job", 
            params={"name": name}, 
            timeout=self._infinite_timeout, 
            op_timeout=self._operation_timeout_seconds
        )

    @safe_call
    @cached(ttl=300, key_prefix="symovo_map")  # Cache for 5 minutes
    async def map(self) -> Dict[str, Any]:
        """Get list of maps."""
        return await self.get("/map")

    @safe_call
    async def map_get(self, map_id: int | str) -> Dict[str, Any]:
        """Get map metadata by ID."""
        return await self.get(f"/map/{map_id}")

    @safe_call
    async def map_wait_for_changes(
        self,
        map_id: int | str,
        *,
        since: str = "now",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for map changes by ID (long-poll)."""
        params: Dict[str, Any] = {"since": since}
        if timeout is not None:
            params["timeout"] = timeout
        op_timeout = (timeout if timeout is not None else settings.transport_watch_timeout) + 5.0
        return await self.get(
            f"/map/{map_id}/wait_for_changes",
            params=params,
            timeout=self._infinite_timeout,
            op_timeout=op_timeout,
            max_retries=0,
        )

    @safe_call
    async def map_tile_png(self, map_id: int | str, zoom: int, x: int, y: int) -> bytes:
        """Get map tile PNG (256x256)."""
        return await self.get_raw(
            f"/map/{zoom}/{x}/{y}.png",
            params={"id": map_id},
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def map_slam_png(self) -> bytes:
        """Get live map preview during SLAM (PNG)."""
        return await self.get_raw(
            "/map/slam/slam.png",
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def map_png(self, map_id: int) -> bytes:
        """Get map as PNG (raw bytes)."""
        return await self.get_raw(
            f"/map/{map_id}/full.png",
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def scan_png(self) -> bytes:
        """Get current laser scan as PNG."""
        return await self._with_endpoint_fallback(
            "get_raw",
            f"/amr/{self.robot_number}/scan.png",
            f"/agv/{self.robot_number}/scan.png",
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def slam_state(self) -> Dict[str, Any]:
        """Get SLAM state."""
        return await self._with_endpoint_fallback(
            "get",
            f"/amr/{self.robot_number}/slam/state",
            f"/agv/{self.robot_number}/slam/state",
        )

    @safe_call
    async def slam_pose_station(self) -> Dict[str, Any]:
        """Get station-based SLAM pose."""
        return await self._with_endpoint_fallback(
            "get",
            f"/amr/{self.robot_number}/slam/pose/station",
            f"/agv/{self.robot_number}/slam/pose/station",
        )

    @safe_call
    async def slam_pose_reflector(self) -> Dict[str, Any]:
        """Get reflector-based SLAM poses."""
        return await self._with_endpoint_fallback(
            "get",
            f"/amr/{self.robot_number}/slam/pose/reflector",
            f"/agv/{self.robot_number}/slam/pose/reflector",
        )

    @safe_call
    @cached(ttl=60, key_prefix="symovo_check_pose")  # Cache for 1 minute
    async def check_pose(self, *, x_m: float, y_m: float, theta_rad: float = 0.0, map_id: Optional[str] = None) -> Dict[str, Any]:
        """Check pose reachability."""
        # Try v0 cost endpoint first; fallback to legacy path and then v1 reachable if available
        pose: Dict[str, Any] = {"x": x_m, "y": y_m, "theta": theta_rad, "map_id": map_id or 0}
        step: Dict[str, Any] = {"poses": [pose], "_type_id": 7}
        transport_payload: Dict[str, Any] = {"steps": [step]}
        try:
            return await self.put(
                f"/amr/{self.robot_number}/get_cost/transport", 
                json_data=transport_payload, 
                op_timeout=self._operation_timeout_seconds
            )
        except DeviceError:
            try:
                # Older firmwares use 'agv' instead of 'amr'
                return await self.put(
                    f"/agv/{self.robot_number}/get_cost/transport", 
                    json_data=transport_payload, 
                    op_timeout=self._operation_timeout_seconds
                )
            except DeviceError:
                # Fallback to reachable endpoint on v1 if present
                root_base = self.base_url
                if root_base.endswith("/v0"):
                    root_base = root_base[:-3]
                reachable_url = f"{root_base}/v1/robots/{self.robot_number}/reachable"
                payload: Dict[str, Any] = {"x": x_m, "y": y_m, "theta": theta_rad}
                if map_id is not None:
                    payload["mapId"] = map_id
                return await self.post(reachable_url, json_data=payload, op_timeout=self._operation_timeout_seconds)

    async def _transport_create_unlocked(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Internal method to create transport without acquiring lock (for use within already-guarded methods)."""
        return await self.post(
            "/transport", 
            json_data=payload, 
            timeout=self._infinite_timeout, 
            op_timeout=self._operation_timeout_seconds
        )

    @guarded_async_call("_navigation_lock", timeout_s=15.0)
    @cache_invalidate("symovo_transport")
    async def transport_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a transport (guarded, serialized)."""
        return await self._transport_create_unlocked(payload)

    def _build_transport_payload(self, steps: list, description: str) -> Dict[str, Any]:
        """Build a Symovo transport payload with common boilerplate."""
        return {
            "timestamp": time.time(),
            "id": 0,
            "agv": {"id": int(self.robot_number)},
            "job": None,
            "steps": steps,
            "state_log": [{
                "timestamp": time.time(),
                "step_idx": 0,
                "status_code": 0,
                "status_detail": 1,
                "level": None,
            }],
            "needed_agv_attributes": {
                "full_eurobox": False,
                "half_eurobox_front": False,
                "half_eurobox_back": False,
                "gap_charge": False,
                "charging_contacts": False,
            },
            "description": description,
            "cancelable": True,
        }

    async def transport_create_station(
        self,
        station_id: int,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a transport to a station using GoToStationStep."""
        step: Dict[str, Any] = {
            "station_id": station_id,
            "_type_id": 1,  # GoToStationStep
            "finished": False,
            "isNext": False,
        }
        payload = self._build_transport_payload(
            [step], description or f"GoTo Station {station_id}",
        )
        return await self.transport_create(payload)

    async def delete_transport(self, transport_id: str) -> bool:
        """Delete a transport from the controller (best-effort cleanup).

        Used to remove orphaned transports that were created but never
        successfully started. Swallows all exceptions so callers in
        error-handling paths are never disrupted.
        """
        try:
            await self.delete(f"/transport/{transport_id}", op_timeout=5.0)
            _LOGGER.warning("Cleaned up orphaned transport %s", transport_id)
            return True
        except Exception as exc:
            _LOGGER.warning("Failed to cleanup orphaned transport %s: %s", transport_id, exc)
            return False

    @guarded_async_call("_navigation_lock", timeout_s=15.0)
    @cache_invalidate("symovo_transport")
    async def transport_start(self, transport_id: str) -> Dict[str, Any]:
        """Start a transport (guarded, serialized)."""
        return await self.put(
            f"/transport/{transport_id}/start",
            timeout=self._infinite_timeout,
            op_timeout=self._operation_timeout_seconds
        )

    @guarded_async_call("_navigation_lock", timeout_s=15.0)
    @cache_invalidate("symovo_transport")
    async def transport_stop(self, transport_id: str) -> Dict[str, Any]:
        """Stop a transport (guarded, serialized)."""
        return await self.put(
            f"/transport/{transport_id}/stop",
            timeout=self._infinite_timeout,
            op_timeout=self._operation_timeout_seconds
        )

    async def transport_move_to_pose(self, *, x_m: float, y_m: float, theta_rad: float = 0.0, map_id: Optional[Any] = None, max_speed_m_s: Optional[float] = None, wait: bool = True) -> Dict[str, Any]:
        """Create a transport task to move the robot to the specified pose.

        Phase 1 (charger disable) runs WITHOUT holding _navigation_lock so safety
        operations (e-stop, pause, cancel) are never blocked.
        Phase 2 (transport create) acquires the lock.
        Polling for completion (wait=True) happens outside the lock.
        """
        # Transport create — guarded
        return await self._transport_move_to_pose_guarded(
            x_m=x_m, y_m=y_m, theta_rad=theta_rad,
            map_id=map_id, max_speed_m_s=max_speed_m_s, wait=wait,
        )

    @guarded_async_call("_navigation_lock", timeout_s=15.0)
    @cache_invalidate("symovo_pose")
    async def _transport_move_to_pose_guarded(self, *, x_m: float, y_m: float, theta_rad: float = 0.0, map_id: Optional[Any] = None, max_speed_m_s: Optional[float] = None, wait: bool = True) -> Dict[str, Any]:
        """Guarded inner method — creates transport under _navigation_lock."""
        pose: Dict[str, Any] = {
            "x": x_m,
            "y": y_m,
            "theta": theta_rad,
            "map_id": map_id or 0,
        }
        if max_speed_m_s is not None:
            pose["maxSpeed"] = max_speed_m_s

        step: Dict[str, Any] = {
            "poses": [pose],
            "_type_id": 7,
            "finished": False,
            "isNext": False,
        }
        payload = self._build_transport_payload([step], "GoTo Pose")

        # Use unlocked version since we're already inside a guarded method (transport_move_to_pose)
        # This prevents nested locking deadlock
        result = await self._transport_create_unlocked(payload)
        if not wait:
            return result
        transport_id = result.get("id") if isinstance(result, dict) else None
        if transport_id is None:
            return result
        # Return only the transport_id; the caller will poll outside the lock.
        # Store it so poll_transport_completion can use it.
        result["_wait_transport_id"] = transport_id
        return result

    async def poll_transport_completion(self, transport_id: Any) -> Dict[str, Any]:
        """Poll for transport completion WITHOUT holding _navigation_lock.

        Called by the route/facade layer after ``transport_move_to_pose`` returns.
        """
        return await self.get(
            f"/transport/{transport_id}",
            timeout=self._infinite_timeout,
            op_timeout=self._motion_timeout_seconds if self._motion_timeout_seconds is not None else self._operation_timeout_seconds,
        )

    @safe_call
    @cached(ttl=10, key_prefix="symovo_transport")  # Cache for 10 seconds
    async def transport_get(self, transport_id: int | str) -> Dict[str, Any]:
        """Get transport task info."""
        return await self.get(f"/transport/{transport_id}")

    @safe_call
    async def transport_get_uncached(self, transport_id: int | str) -> Dict[str, Any]:
        """Get transport task info (uncached)."""
        return await self.get(f"/transport/{transport_id}")

    @safe_call
    async def transport_wait_for_changes(
        self,
        transport_id: int | str,
        *,
        since: str = "now",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for transport task changes (long-poll).

        Retries are disabled (max_retries=0) because the caller
        (_watch_transport) manages its own retry/backoff loop.  Internal
        retries used to block the loop for 4×35 s = 147 s, preventing
        force-arrived from being consumed.
        """
        params: Dict[str, Any] = {"since": since}
        if timeout is not None:
            params["timeout"] = timeout
        # Client-side cap protects against hung connections even if server ignores long-poll timeout.
        op_timeout = (timeout if timeout is not None else settings.transport_watch_timeout) + 5.0
        started = time.perf_counter()
        try:
            result = await self.get(
                f"/transport/{transport_id}/wait_for_changes",
                params=params,
                timeout=self._infinite_timeout,
                op_timeout=op_timeout,
                max_retries=0,
            )
            reliability_metrics.observe_duration(
                "symovo.transport_wait_for_changes.latency_s",
                time.perf_counter() - started,
            )
            return result
        except (DeviceError, DeviceConnectionError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            reliability_metrics.inc("symovo.transport_wait_for_changes.error")
            if isinstance(e, asyncio.TimeoutError) or "timeout" in str(e).lower():
                reliability_metrics.inc("symovo.transport_wait_for_changes.timeout")
            raise

    @safe_call
    async def transports_wait_for_changes(self) -> Dict[str, Any]:
        """Wait for changes in any transport tasks."""
        return await self.get(
            "/transport/wait_for_changes",
            op_timeout=settings.transport_watch_timeout + 5.0,
        )

    @safe_call
    async def amr_wait_for_changes(
        self,
        *,
        since: str = "now",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for AMR changes (long-poll)."""
        params: Dict[str, Any] = {"since": since}
        if timeout is not None:
            params["timeout"] = timeout
        op_timeout = (timeout if timeout is not None else settings.transport_watch_timeout) + 5.0
        started = time.perf_counter()
        # Prefer spec path; try both for firmware compatibility
        try:
            result = await self.get(
                f"/amr/{self.robot_number}/wait_for_changes",
                params=params,
                timeout=self._infinite_timeout,
                op_timeout=op_timeout,
            )
            reliability_metrics.observe_duration(
                "symovo.amr_wait_for_changes.latency_s",
                time.perf_counter() - started,
            )
            return result
        except (DeviceError, DeviceConnectionError, aiohttp.ClientError, asyncio.TimeoutError):
            try:
                result = await self.get(
                    f"/agv/{self.robot_number}/wait_for_changes",
                    params=params,
                    timeout=self._infinite_timeout,
                    op_timeout=op_timeout,
                )
                reliability_metrics.observe_duration(
                    "symovo.amr_wait_for_changes.latency_s",
                    time.perf_counter() - started,
                )
                return result
            except (DeviceError, DeviceConnectionError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                reliability_metrics.inc("symovo.amr_wait_for_changes.error")
                if isinstance(e, asyncio.TimeoutError) or "timeout" in str(e).lower():
                    reliability_metrics.inc("symovo.amr_wait_for_changes.timeout")
                raise

    @safe_call
    @cached(ttl=60, key_prefix="symovo_stations")  # Cache for 1 minute
    async def get_stations(self) -> List[Dict[str, Any]]:
        """Get list of all stations."""
        result = await self.get("/station")
        # If server returned a dict with "result" key or a string, unwrap it
        if isinstance(result, dict):
            if "result" in result and isinstance(result["result"], list):
                return result["result"]
            # sometimes it may be a single station object
            return [result]
        elif isinstance(result, list):
            return result
        else:
            return []

    @safe_call
    @cached(ttl=60, key_prefix="symovo_charging_stations")  # Cache for 1 minute
    async def get_charging_stations(self) -> List[Dict[str, Any]]:
        """Get only charging stations."""
        stations = await self.get_stations()
        return [s for s in stations if isinstance(s, dict) and s.get("_type_id") == 4]

    @safe_call
    @cache_invalidate("symovo_station")  # Invalidate stations and charging stations cache (covers both symovo_stations and symovo_charging_stations)
    async def set_charging_station_enabled(self, station_id: str, active: bool) -> Dict[str, Any]:
        """Enable/disable a charging station."""
        station = await self.get(f"/station/{station_id}")
        payload = {
            "id": station["id"],
            "name": station.get("name", ""),
            "description": station.get("description", ""),
            "pose": station.get("pose", {}),
            "state": "OK" if active else "INACTIVE",
            "parking_allowed": station.get("parking_allowed", True),
            "has_charger": station.get("has_charger", True),
            "agv": None,   # explicitly set null on update
            "container_id": None,
            "barcode": None,
            "pos_tolerance": None,
            "iot_device": None,
            "_type_id": station.get("_type_id", 4),
            "user_offset": station.get("user_offset", 0),
        }
        return await self.put(f"/station/{station_id}", json_data=payload)

    @safe_call
    @cache_invalidate("symovo_charging_stations")  # Invalidate charging stations cache
    async def disable_all_charging_stations(self) -> List[Dict[str, Any]]:
        """Deactivate all charging stations."""
        stations = await self.get_charging_stations()
        results = []
        for st in stations:
            if st.get("state") != "INACTIVE":
                res = await self.set_charging_station_enabled(st["id"], False)
                results.append(res)
        return results

    @safe_call
    @cache_invalidate("symovo_charging_stations")  # Invalidate charging stations cache
    async def enable_all_charging_stations(self) -> List[Dict[str, Any]]:
        """Activate all charging stations."""
        stations = await self.get_charging_stations()
        results = []
        for st in stations:
            if st.get("state") != "OK":
                res = await self.set_charging_station_enabled(st["id"], True)
                results.append(res)
        return results

    @safe_call
    async def set_charging_station_enabled_by_name(self, name: str, enabled: bool = True) -> Optional[int]:
        # Enable/disable a charging station by its name (case-insensitive).
        # Returns station id if found and request is sent.
        stations = await self.get_charging_stations()
        target = name.strip().lower()
        
        _LOGGER.debug(
            "Searching for charging station by name: '%s' (case-insensitive). Found %d charging station(s) total.",
            name,
            len(stations),
        )
        
        if not stations:
            _LOGGER.warning("No charging stations found on controller. Check controller connectivity and station configuration.")
            return None
        
        # Log all available station names for debugging
        available_names = [str(st.get("name", "")).strip() for st in stations if st.get("name")]
        _LOGGER.debug("Available charging station names: %s", available_names)
        
        for st in stations:
            st_name = str(st.get("name", "")).strip().lower()
            if st_name == target:
                station_id = int(st["id"])
                _LOGGER.info(
                    "Found charging station: name='%s' id=%d. Setting enabled=%s",
                    st.get("name"),
                    station_id,
                    enabled,
                )
                await self.set_charging_station_enabled(station_id, enabled)
                return station_id
        
        _LOGGER.warning(
            "Charging station not found by name '%s'. Available names: %s. "
            "Check CHARGER_STATION_NAME setting matches station name on controller.",
            name,
            available_names,
        )
        return None

    async def wait_until_charging_stations_inactive(self, timeout: float = 5.0, interval: float = 0.5) -> bool:
        """Wait until all charging stations become INACTIVE.

        Bypasses the @cached get_charging_stations() to see real-time state.
        """
        start = time.time()
        while time.time() - start < timeout:
            # Fetch stations via raw GET (bypass both @cached get_stations and get_charging_stations)
            result = await self.get("/station")
            if isinstance(result, dict) and "result" in result and isinstance(result["result"], list):
                all_stations = result["result"]
            elif isinstance(result, list):
                all_stations = result
            else:
                all_stations = [result] if isinstance(result, dict) else []
            stations = [s for s in all_stations if isinstance(s, dict) and s.get("_type_id") == 4]
            if all(st.get("state") == "INACTIVE" for st in stations):
                return True
            await asyncio.sleep(interval)
        return False
    
    @guarded_async_call("_admin_lock", timeout_s=15.0)
    @cache_invalidate("symovo_transport")
    async def clear_all_transports(self) -> List[Dict[str, Any]]:
        """Delete all transport tasks (guarded, serialized)."""
        request_timeout = 5.0
        total_timeout = float(settings.clear_transports_timeout_s)

        _LOGGER.info("Fetching transport list...")
        try:
            result = await self.get("/transport", op_timeout=request_timeout)
        except _FALLBACK_EXCEPTIONS as e:
            _LOGGER.warning("Failed to fetch transport list: %s", e)
            return []

        transports: List[Dict[str, Any]]
        if isinstance(result, dict) and "result" in result:
            transports = result["result"]
        elif isinstance(result, list):
            transports = result
        else:
            _LOGGER.info("No transports found or invalid response format")
            return []

        transport_count = len(transports)
        _LOGGER.info("Found %d transport(s) to delete", transport_count)

        if transport_count == 0:
            return []

        deleted: List[Dict[str, Any]] = []
        _sem = asyncio.Semaphore(5)

        async def _delete_one(tid: int, idx: int) -> Optional[Dict[str, Any]]:
            async with _sem:
                try:
                    if transport_count > 5 or idx % 5 == 0 or idx == transport_count:
                        _LOGGER.info("Deleting transport %s (%d/%d)...", tid, idx, transport_count)
                    resp = await self._make_request("DELETE", f"/transport/{tid}", op_timeout=request_timeout, max_retries=0)
                    return {"id": tid, "status": resp}
                except _FALLBACK_EXCEPTIONS as e:
                    _LOGGER.warning("Failed to delete transport %s: %s", tid, e)
                    return None

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[_delete_one(t.get("id"), idx) for idx, t in enumerate(transports, 1) if t.get("id") is not None],
                    return_exceptions=True,
                ),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "clear_all_transports: total timeout (%.0fs) exceeded with %d transports",
                total_timeout, transport_count,
            )
            return deleted

        for r in results:
            if isinstance(r, dict):
                deleted.append(r)

        _LOGGER.info("Verifying deletion by fetching transport list again...")
        try:
            result = await self.get("/transport", op_timeout=request_timeout)
            remaining = result.get("result", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
            _LOGGER.info("Deletion complete. Deleted %d transport(s), remaining: %d", len(deleted), len(remaining))
            return deleted
        except _FALLBACK_EXCEPTIONS as e:
            _LOGGER.warning("Failed to verify deletion: %s", e)
            return deleted


# Old main removed - now using dependency injection
