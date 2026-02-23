"""
Оптимизированный сервис для работы с Symovo AGV.
"""
import asyncio
import logging
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

# Глобальный lock для синхронизации операций
symovo_lock = asyncio.Lock()
_LOGGER = logging.getLogger(__name__)


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
        
        # Конвертируем радианы в градусы для theta
        theta_rad = pose.get("theta")
        theta_deg = None
        if theta_rad is not None and isinstance(theta_rad, (int, float)):
            theta_deg = theta_rad * 180.0 / 3.141592653589793  # pi
        
        # Конвертируем угловую скорость из рад/с в град/с
        omega_rad_s = velocity.get("theta")
        omega_deg_s = None
        if omega_rad_s is not None and isinstance(omega_rad_s, (int, float)):
            omega_deg_s = omega_rad_s * 180.0 / 3.141592653589793  # pi
        
        normalized = {
            "online": True,
            "last_update_time": None,
            "id": str(raw.get("id")) if raw.get("id") is not None else None,
            "name": raw.get("name"),
            "pose": {
                "x_m": pose.get("x"),
                "y_m": pose.get("y"),
                "theta_deg": theta_deg,  # Теперь возвращаем градусы
                "map_id": pose.get("map_id"),
            },
            "velocity": {
                "vx_m_s": velocity.get("x"),
                "vy_m_s": velocity.get("y"),
                "omega_deg_s": omega_deg_s,  # Теперь возвращаем градусы в секунду
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
    except Exception as e:
        return ErrorStatus(error={"type": "InvalidSymovoStatus", "msg": str(e), "raw": raw})


class SymovoAgvClient(BaseHttpClient):
    """Оптимизированный клиент для работы с Symovo AGV."""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        robot_number: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        operation_timeout_seconds: Optional[float] = None,
        motion_timeout_seconds: Optional[float] = None,
        allow_invalid_certs: Optional[bool] = None
    ):
        # Используем настройки из конфига по умолчанию
        base_url = base_url or settings.symovo_base_url
        robot_number = robot_number or settings.symovo_robot_number
        timeout_seconds = timeout_seconds or settings.symovo_timeout_seconds
        allow_invalid_certs = allow_invalid_certs if allow_invalid_certs is not None else settings.symovo_allow_invalid_certs
        
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            allow_invalid_certs=allow_invalid_certs
        )
        
        self.robot_number = robot_number
        self._operation_timeout_seconds = operation_timeout_seconds or settings.symovo_operation_timeout_seconds
        self._motion_timeout_seconds = motion_timeout_seconds or settings.symovo_motion_timeout_seconds
        self._infinite_timeout = aiohttp.ClientTimeout(total=None)

    # Удаляем старые методы управления сессией - теперь используем BaseHttpClient

    # HTTP методы теперь наследуются от BaseHttpClient

    @safe_call
    @cached(ttl=1, key_prefix="symovo_pose")
    async def pose(self) -> Dict[str, Any]:
        """Получить текущую позицию AGV (с кешем 1с для UI/маршрутов)."""
        return await self._pose_impl()

    @safe_call
    async def pose_uncached(self) -> Dict[str, Any]:
        """Получить текущую позицию AGV (без кеша) для фоновых циклов."""
        return await self._pose_impl()

    async def _pose_impl(self) -> Dict[str, Any]:
        """Internal: fetch pose with agv/amr fallback."""
        try:
            return await self.get(f"/agv/{self.robot_number}/pose")
        except Exception:
            return await self.get(f"/amr/{self.robot_number}/pose")

    @safe_call
    @cached(ttl=1, key_prefix="symovo_status")
    async def status(self) -> Dict[str, Any]:
        """Получить статус AGV (с кешем 1с для UI/маршрутов)."""
        return await self._status_impl()

    @safe_call
    async def status_uncached(self) -> Dict[str, Any]:
        """Получить статус AGV (без кеша), чтобы сразу увидеть изменения флагов."""
        return await self._status_impl()

    async def _status_impl(self) -> Dict[str, Any]:
        """Internal: fetch status with agv/amr fallback."""
        try:
            return await self.get(f"/agv/{self.robot_number}")
        except Exception:
            return await self.get(f"/amr/{self.robot_number}")

    @guarded_async_call(symovo_lock, timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def set_drive_mode(self, *, enable: bool = True) -> Dict[str, Any]:
        """
        Switch AMR into drive mode (enable/disable motors).

        Some Symovo controllers require a JSON payload: {"enable": true}.
        Observed working request:
          PUT /v0/agv/{id}/move/drive_mode  body={"enable": true}  -> 202 Accepted

        When enable=True, the charging station (CHARGER_STATION_NAME) is deactivated
        if charger workflow is enabled — mirror of "activate charger when navigating to CHARGER".
        """
        payload: Dict[str, Any] = {"enable": bool(enable)}

        if enable:
            from services import charger_workflow
            await charger_workflow.maybe_deactivate_on_drive_mode(self)

        # Prefer 'agv' path first (matches observed controller behavior); fallback to 'amr'.
        try:
            return await self.put(f"/agv/{self.robot_number}/move/drive_mode", json_data=payload)
        except Exception:
            return await self.put(f"/amr/{self.robot_number}/move/drive_mode", json_data=payload)

    async def move_speed(
        self,
        speed: Optional[float] = None,
        angular_speed: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Отправка скорости для телеуправления (джойстик/клавиатура).
        OpenAPI: PUT /v0/agv/{id}/move/speed — body MoveSpeed.
        Запрос блокируется до окончания движения (ответ 202).
        Не использует symovo_lock, чтобы не блокировать навигацию.
        """
        effective_speed = float(speed) if speed is not None else float(settings.teleop_default_linear_speed)
        effective_angular = float(angular_speed) if angular_speed is not None else float(settings.teleop_default_angular_speed)
        effective_duration = float(duration) if duration is not None else float(settings.teleop_default_duration)
        payload: Dict[str, Any] = {
            "speed": effective_speed,
            "angular_speed": effective_angular,
            "duration": effective_duration,
        }
        # Таймаут чуть больше длительности команды
        op_timeout = max(1.0, effective_duration + 2.0)
        try:
            return await self.put(
                f"/agv/{self.robot_number}/move/speed",
                json_data=payload,
                op_timeout=op_timeout,
            )
        except Exception:
            return await self.put(
                f"/amr/{self.robot_number}/move/speed",
                json_data=payload,
                op_timeout=op_timeout,
            )

    @guarded_async_call(symovo_lock, timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def pause_stop(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/pause/stop"""
        try:
            return await self.put(f"/amr/{self.robot_number}/pause/stop")
        except Exception:
            return await self.put(f"/agv/{self.robot_number}/pause/stop")

    @guarded_async_call(symovo_lock, timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def pause_start(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/pause/start"""
        try:
            return await self.put(f"/amr/{self.robot_number}/pause/start")
        except Exception:
            return await self.put(f"/agv/{self.robot_number}/pause/start")

    @guarded_async_call(symovo_lock, timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def reset_emergency_stop(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/amr/{id}/safety/reset_emergency_stop"""
        try:
            return await self.put(f"/amr/{self.robot_number}/safety/reset_emergency_stop")
        except Exception:
            return await self.put(f"/agv/{self.robot_number}/safety/reset_emergency_stop")

    @guarded_async_call(symovo_lock, timeout_s=5.0)
    @cache_invalidate("symovo_status")
    async def reset_software_fuse(self) -> Dict[str, Any]:
        """OpenAPI: PUT /v0/agv/{id}/software_fuse/reset

        Resets the software fuse (sfuse_blown flag) after a high motor
        current event.  The controller returns 202 on success.
        """
        try:
            return await self.put(f"/agv/{self.robot_number}/software_fuse/reset")
        except Exception:
            return await self.put(f"/amr/{self.robot_number}/software_fuse/reset")

    @safe_call
    @cached(ttl=30, key_prefix="symovo_job")  # Кешируем на 30 секунд
    async def job(self) -> Dict[str, Any]:
        """Получить информацию о текущей задаче."""
        return await self.get("/job")

    @guarded_async_call(symovo_lock)
    @cache_invalidate("symovo_job")  # Инвалидируем кеш задач
    async def create_new_job(self, name: str) -> Dict[str, Any]:
        """Создать новую задачу."""
        return await self.get(
            "/new_job", 
            params={"name": name}, 
            timeout=self._infinite_timeout, 
            op_timeout=self._operation_timeout_seconds
        )

    @safe_call
    @cached(ttl=300, key_prefix="symovo_map")  # Кешируем на 5 минут
    async def map(self) -> Dict[str, Any]:
        """Получить список карт."""
        return await self.get("/map")

    @safe_call
    async def map_get(self, map_id: int | str) -> Dict[str, Any]:
        """Получить метаданные карты по ID."""
        return await self.get(f"/map/{map_id}")

    @safe_call
    async def map_wait_for_changes(
        self,
        map_id: int | str,
        *,
        since: str = "now",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ожидать изменения карты по ID (long-poll)."""
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
        """Получить map tile PNG (256x256)."""
        return await self.get_raw(
            f"/map/{zoom}/{x}/{y}.png",
            params={"id": map_id},
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def map_slam_png(self) -> bytes:
        """Получить live preview карты во время SLAM (PNG)."""
        return await self.get_raw(
            "/map/slam/slam.png",
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def map_png(self, map_id: int) -> bytes:
        """Получить карту в формате PNG (сырые байты)."""
        return await self.get_raw(
            f"/map/{map_id}/full.png",
            op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
        )

    @safe_call
    async def scan_png(self) -> bytes:
        """Получить текущий laser scan как PNG."""
        try:
            return await self.get_raw(
                f"/amr/{self.robot_number}/scan.png",
                op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
            )
        except Exception:
            return await self.get_raw(
                f"/agv/{self.robot_number}/scan.png",
                op_timeout=max(5.0, float(settings.symovo_timeout_seconds)),
            )

    @safe_call
    async def slam_state(self) -> Dict[str, Any]:
        """Получить состояние SLAM."""
        try:
            return await self.get(f"/amr/{self.robot_number}/slam/state")
        except Exception:
            return await self.get(f"/agv/{self.robot_number}/slam/state")

    @safe_call
    async def slam_pose_station(self) -> Dict[str, Any]:
        """Получить station-based SLAM pose."""
        try:
            return await self.get(f"/amr/{self.robot_number}/slam/pose/station")
        except Exception:
            return await self.get(f"/agv/{self.robot_number}/slam/pose/station")

    @safe_call
    async def slam_pose_reflector(self) -> Dict[str, Any]:
        """Получить reflector-based SLAM poses."""
        try:
            return await self.get(f"/amr/{self.robot_number}/slam/pose/reflector")
        except Exception:
            return await self.get(f"/agv/{self.robot_number}/slam/pose/reflector")

    @safe_call
    @cached(ttl=60, key_prefix="symovo_check_pose")  # Кешируем на 1 минуту
    async def check_pose(self, *, x_m: float, y_m: float, theta_rad: float = 0.0, map_id: Optional[str] = None) -> Dict[str, Any]:
        """Проверить доступность позиции."""
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

    @guarded_async_call(symovo_lock, timeout_s=15.0)  # Increased timeout - clear_all_transports may take time
    @cache_invalidate("symovo_transport")  # Инвалидируем кеш транспортов
    async def transport_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a transport (guarded, serialized)."""
        return await self._transport_create_unlocked(payload)

    @guarded_async_call(symovo_lock, timeout_s=15.0)  # Increased timeout for lock acquisition
    @cache_invalidate("symovo_transport")  # Инвалидируем кеш транспортов
    async def transport_start(self, transport_id: str) -> Dict[str, Any]:
        """Start a transport (guarded, serialized)."""
        return await self.put(
            f"/transport/{transport_id}/start",
            timeout=self._infinite_timeout,
            op_timeout=self._operation_timeout_seconds
        )

    @guarded_async_call(symovo_lock, timeout_s=15.0)  # Increased timeout for lock acquisition
    @cache_invalidate("symovo_transport")  # Инвалидируем кеш транспортов
    async def transport_stop(self, transport_id: str) -> Dict[str, Any]:
        """Stop a transport (guarded, serialized)."""
        return await self.put(
            f"/transport/{transport_id}/stop",
            timeout=self._infinite_timeout,
            op_timeout=self._operation_timeout_seconds
        )

    @guarded_async_call(symovo_lock, timeout_s=15.0)  # Increased timeout - may call transport_create internally
    @cache_invalidate("symovo_pose")  # Инвалидируем кеш позиции
    async def transport_move_to_pose(self, *, x_m: float, y_m: float, theta_rad: float = 0.0, map_id: Optional[Any] = None, max_speed_m_s: Optional[float] = None, wait: bool = True) -> Dict[str, Any]:
        """Создать транспортную задачу для перемещения в позицию."""
        from services import charger_workflow
        await charger_workflow.disable_all_before_move(self)
        
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
        state_log_item: Dict[str, Any] = {
            "timestamp": time.time(),
            "step_idx": 0,
            "status_code": 0,
            "status_detail": 1,
            "level": None,
        }
        payload: Dict[str, Any] = {
            "timestamp": time.time(),
            "id": 0,
            "agv": {"id": int(self.robot_number)},
            "job": None,
            "steps": [step],
            "state_log": [state_log_item],
            "needed_agv_attributes": {
                "full_eurobox": False,
                "half_eurobox_front": False,
                "half_eurobox_back": False,
                "gap_charge": False,
                "charging_contacts": False,
            },
            "description": "GoTo Pose",
            "cancelable": True,
        }

        # Use unlocked version since we're already inside a guarded method (transport_move_to_pose)
        # This prevents nested locking deadlock
        result = await self._transport_create_unlocked(payload)
        if not wait:
            return result
        transport_id = result.get("id") if isinstance(result, dict) else None
        if transport_id is None:
            return result
        return await self.get(
            f"/transport/{transport_id}", 
            timeout=self._infinite_timeout, 
            op_timeout=self._motion_timeout_seconds or self._operation_timeout_seconds
        )

    @safe_call
    @cached(ttl=10, key_prefix="symovo_transport")  # Кешируем на 10 секунд
    async def transport_get(self, transport_id: int | str) -> Dict[str, Any]:
        """Получить информацию о транспортной задаче."""
        return await self.get(f"/transport/{transport_id}")

    @safe_call
    async def transport_get_uncached(self, transport_id: int | str) -> Dict[str, Any]:
        """Получить информацию о транспортной задаче (без кеша)."""
        return await self.get(f"/transport/{transport_id}")

    @safe_call
    async def transport_wait_for_changes(
        self,
        transport_id: int | str,
        *,
        since: str = "now",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ожидать изменения в транспортной задаче (long-poll).

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
        except Exception as e:
            reliability_metrics.inc("symovo.transport_wait_for_changes.error")
            if "timeout" in str(e).lower():
                reliability_metrics.inc("symovo.transport_wait_for_changes.timeout")
            raise

    @safe_call
    async def transports_wait_for_changes(self) -> Dict[str, Any]:
        """Ожидать изменения в любых транспортных задачах."""
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
        """Ожидать изменения по AMR (long-poll)."""
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
        except Exception:
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
            except Exception as e:
                reliability_metrics.inc("symovo.amr_wait_for_changes.error")
                if "timeout" in str(e).lower():
                    reliability_metrics.inc("symovo.amr_wait_for_changes.timeout")
                raise

    @safe_call
    @cached(ttl=60, key_prefix="symovo_stations")  # Кешируем на 1 минуту
    async def get_stations(self) -> List[Dict[str, Any]]:
        """Получить список всех станций."""
        result = await self.get("/station")
        # Если сервер вернул словарь с ключом "result" или строку — разворачиваем
        if isinstance(result, dict):
            if "result" in result and isinstance(result["result"], list):
                return result["result"]
            # иногда может быть объект одной станции
            return [result]
        elif isinstance(result, list):
            return result
        else:
            return []

    @safe_call
    @cached(ttl=60, key_prefix="symovo_charging_stations")  # Кешируем на 1 минуту
    async def get_charging_stations(self) -> List[Dict[str, Any]]:
        """Получить только зарядные станции."""
        stations = await self.get_stations()
        return [s for s in stations if isinstance(s, dict) and s.get("_type_id") == 4]

    @safe_call
    @cache_invalidate("symovo_station")  # Инвалидируем кеш станций и зарядных станций (covers both symovo_stations and symovo_charging_stations)
    async def set_charging_station_enabled(self, station_id: str, active: bool) -> Dict[str, Any]:
        """Активировать/деактивировать зарядную станцию."""
        station = await self.get(f"/station/{station_id}")
        payload = {
            "id": station["id"],
            "name": station.get("name", ""),
            "description": station.get("description", ""),
            "pose": station.get("pose", {}),
            "state": "OK" if active else "INACTIVE",
            "parking_allowed": station.get("parking_allowed", True),
            "has_charger": station.get("has_charger", True),
            "agv": None,   # при update явно указываем null
            "container_id": None,
            "barcode": None,
            "pos_tolerance": None,
            "iot_device": None,
            "_type_id": station.get("_type_id", 4),
            "user_offset": station.get("user_offset", 0),
        }
        return await self.put(f"/station/{station_id}", json_data=payload)

    @safe_call
    @cache_invalidate("symovo_charging_stations")  # Инвалидируем кеш зарядных станций
    async def disable_all_charging_stations(self) -> List[Dict[str, Any]]:
        """Деактивировать все зарядные станции."""
        stations = await self.get_charging_stations()
        results = []
        for st in stations:
            if st.get("state") != "INACTIVE":
                res = await self.set_charging_station_enabled(st["id"], False)
                results.append(res)
        return results

    @safe_call
    @cache_invalidate("symovo_charging_stations")  # Инвалидируем кеш зарядных станций
    async def enable_all_charging_stations(self) -> List[Dict[str, Any]]:
        """Активировать все зарядные станции."""
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
        import logging
        _logger = logging.getLogger(__name__)
        
        stations = await self.get_charging_stations()
        target = name.strip().lower()
        
        _logger.debug(
            "Searching for charging station by name: '%s' (case-insensitive). Found %d charging station(s) total.",
            name,
            len(stations),
        )
        
        if not stations:
            _logger.warning("No charging stations found on controller. Check controller connectivity and station configuration.")
            return None
        
        # Log all available station names for debugging
        available_names = [str(st.get("name", "")).strip() for st in stations if st.get("name")]
        _logger.debug("Available charging station names: %s", available_names)
        
        for st in stations:
            st_name = str(st.get("name", "")).strip().lower()
            if st_name == target:
                station_id = int(st["id"])
                _logger.info(
                    "Found charging station: name='%s' id=%d. Setting enabled=%s",
                    st.get("name"),
                    station_id,
                    enabled,
                )
                await self.set_charging_station_enabled(station_id, enabled)
                return station_id
        
        _logger.warning(
            "Charging station not found by name '%s'. Available names: %s. "
            "Check CHARGER_STATION_NAME setting matches station name on controller.",
            name,
            available_names,
        )
        return None

    async def wait_until_charging_stations_inactive(self, timeout: float = 5.0, interval: float = 0.5) -> bool:
        """Ждём, пока все зарядные станции не станут INACTIVE."""
        start = time.time()
        while time.time() - start < timeout:
            stations = await self.get_charging_stations()
            if all(st.get("state") == "INACTIVE" for st in stations):
                return True
            await asyncio.sleep(interval)
        return False
    
    @guarded_async_call(symovo_lock, timeout_s=15.0)  # Increased timeout - multiple HTTP requests may take time
    @cache_invalidate("symovo_transport")  # Инвалидируем кеш транспортов
    async def clear_all_transports(self) -> List[Dict[str, Any]]:
        """Удалить все транспортные задачи (guarded, serialized)."""
        import logging
        _logger = logging.getLogger(__name__)
        
        # Use shorter timeout per request (5 seconds) to avoid hanging on individual requests
        request_timeout = 5.0
        
        _logger.info("Fetching transport list...")
        try:
            result = await self.get("/transport", op_timeout=request_timeout)
        except Exception as e:
            _logger.warning(f"Failed to fetch transport list: {e}")
            return []

        # Нормализуем список
        transports: List[Dict[str, Any]]
        if isinstance(result, dict) and "result" in result:
            transports = result["result"]
        elif isinstance(result, list):
            transports = result
        else:
            _logger.info("No transports found or invalid response format")
            return []

        transport_count = len(transports)
        _logger.info(f"Found {transport_count} transport(s) to delete")
        
        if transport_count == 0:
            return []

        deleted = []
        for idx, t in enumerate(transports, 1):
            tid = t.get("id")
            if tid is not None:
                try:
                    if transport_count > 5 or idx % 5 == 0 or idx == transport_count:
                        _logger.info(f"Deleting transport {tid} ({idx}/{transport_count})...")
                    resp = await self.delete(f"/transport/{tid}", op_timeout=request_timeout)
                    deleted.append({"id": tid, "status": resp})
                except Exception as e:
                    _logger.warning(f"Failed to delete transport {tid}: {e}")
                    # Continue with other transports even if one fails
        
        _logger.info("Verifying deletion by fetching transport list again...")
        try:
            result = await self.get("/transport", op_timeout=request_timeout)
            remaining = result.get("result", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
            _logger.info(f"Deletion complete. Deleted {len(deleted)} transport(s), remaining: {len(remaining)}")
            # P1-5 fix: Return *deleted* transports (what we removed), not *remaining*.
            # Callers log the result as "cleared N transports" — 0 should mean "nothing to clear",
            # not "all deletions succeeded".
            return deleted
        except Exception as e:
            _logger.warning(f"Failed to verify deletion: {e}")
            return deleted


# Удален старый main - теперь используется dependency injection
