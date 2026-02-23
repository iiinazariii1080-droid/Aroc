from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app import error_codes
from app import config as app_config
from app.api_models import (
    CiA402State,
    DriveOnlineState,
    DriveStatus,
    FaultInfo,
    FaultResetRequest,
    JogMoveRequest,
    MoveToPositionRequest,
    OperationMode,
    ReferenceRequest,
    StopRequest,
    driver_cia402_state_to_api_state,
    mode_display_to_operation_mode,
)
from app.application.drive_service import DriveService, ServiceError
from app.cache import StateCache
from drivers.dryve_d1.od.indices import ODIndex
from drivers.dryve_d1.protocol.exceptions import MotionAborted

if TYPE_CHECKING:
    from app.protocols import AppStateProtocol
    from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State


@dataclass(frozen=True)
class _ResolvedSnapshot:
    statusword: int
    position: float | int | None
    velocity: float | int | None
    cia402_state_raw: DriverCiA402State
    cached: dict[str, Any] | None
    poll_info: dict[str, Any]
    decoded_status: dict[str, bool] | None = None
    mode_display_raw: int | None = None


class DriveUseCases:
    def __init__(self, app_state: AppStateProtocol) -> None:
        self._service = DriveService(app_state)

    def _raise_translated(self, op: str, exc: Exception) -> None:
        status_code, detail = DriveService.translate_driver_exception(op, exc)
        raise ServiceError(
            status_code=status_code,
            code=str(detail.get("code", "INTERNAL_ERROR")),
            message=str(detail.get("error", f"{op} failed")),
        )

    @staticmethod
    def _ensure_op_id(op_id: str | None) -> str:
        return op_id or uuid.uuid4().hex[:8]

    @staticmethod
    def _as_float_or_none(value) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _resolve_jog_speed(speed: float | None) -> int:
        if speed is None:
            return int(getattr(app_config, "DRYVE_JOG_DEFAULT_SPEED", 2000))
        return int(speed)

    async def _resolve_snapshot(self, *, op: str) -> _ResolvedSnapshot:
        drive = self._service.get_drive()
        cache = StateCache(drive)
        cached = cache.get_cached_status()
        poll_info = cache.get_poll_info()

        if cached is None:
            try:
                statusword = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
                position = await drive.get_position()
                velocity_raw = await drive.read_i32(int(ODIndex.VELOCITY_ACTUAL_VALUE), 0)

                from drivers.dryve_d1.od.statusword import infer_cia402_state

                cia402_state_raw = infer_cia402_state(statusword)

                return _ResolvedSnapshot(
                    statusword=statusword,
                    position=position,
                    velocity=velocity_raw,
                    cia402_state_raw=cia402_state_raw,
                    cached=None,
                    poll_info=poll_info,
                )
            except Exception as exc:
                if op == "status":
                    raise ServiceError(
                        503,
                        error_codes.STATUS_READ_FAILED.code,
                        f"{error_codes.STATUS_READ_FAILED.message}: {exc!s}",
                    ) from exc
                raise ServiceError(
                    503,
                    error_codes.TELEMETRY_READ_FAILED.code,
                    f"{error_codes.TELEMETRY_READ_FAILED.message}: {exc!s}",
                ) from exc

        return _ResolvedSnapshot(
            statusword=cached["statusword"],
            position=cached["position"],
            velocity=cached["velocity"],
            cia402_state_raw=cached["cia402_state"],
            cached=cached,
            poll_info=poll_info,
            decoded_status=cached.get("decoded_status"),
            mode_display_raw=cached.get("mode_display"),
        )

    async def _status_fields(
        self, snapshot: _ResolvedSnapshot,
    ) -> tuple[dict[str, bool], OperationMode, CiA402State]:
        drive = self._service.get_drive()
        status_dict = snapshot.decoded_status
        mode_display_raw = snapshot.mode_display_raw

        if status_dict is None:
            status_dict = await drive.get_status()
        if mode_display_raw is None:
            mode_display_raw = await drive.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)

        if snapshot.cached is None:
            cia402_state = driver_cia402_state_to_api_state(snapshot.cia402_state_raw)
        else:
            cia402_state = driver_cia402_state_to_api_state(snapshot.cached["cia402_state"])

        mode_display = mode_display_to_operation_mode(mode_display_raw)
        return status_dict or {}, mode_display, cia402_state

    async def get_drive_status(self) -> DriveStatus:
        drive = self._service.get_drive()
        snapshot = await self._resolve_snapshot(op="status")
        cached = snapshot.cached
        poll_info = snapshot.poll_info

        if not drive.is_connected:
            online_state = DriveOnlineState.OFFLINE
        elif cached is None:
            online_state = DriveOnlineState.DEGRADED
        else:
            online_state = DriveOnlineState.ONLINE

        statusword = snapshot.statusword
        position = snapshot.position
        velocity_raw = snapshot.velocity
        status_dict, mode_display, cia402_state = await self._status_fields(snapshot)

        has_fault = status_dict.get("fault", False)
        fault_details = None
        if has_fault:
            diag = await self._service.read_fault_info()
            from app.api_models import FaultDetails

            fault_details = FaultDetails(
                error_code=diag.get("error_code"),
                error_register=diag.get("error_register"),
                history=diag.get("history"),
            )
        fault_info = FaultInfo(active=has_fault, code=None, description=None, details=fault_details)

        poll_period_ms = None
        poll_latency_ms = None
        last_poll_ts = None
        if cached is not None and "ts_monotonic_s" in cached:
            poll_period_ms = poll_info.get("interval_s", 0.5) * 1000
            last_poll_ts = int(cached["ts_monotonic_s"] * 1000)

        return DriveStatus(
            online=online_state,
            last_poll_ts=last_poll_ts,
            poll_period_ms=poll_period_ms,
            poll_latency_ms=poll_latency_ms,
            cia402_state=cia402_state,
            mode_display=mode_display,
            statusword=statusword,
            controlword=None,
            status_bits=status_dict,
            remote=status_dict.get("remote"),
            enabled=status_dict.get("operation_enabled"),
            position=self._as_float_or_none(position),
            velocity=self._as_float_or_none(velocity_raw),
            torque=None,
            fault=fault_info,
            last_error=None,
        )

    async def get_drive_telemetry(self) -> dict:
        snapshot = await self._resolve_snapshot(op="telemetry")
        cached = snapshot.cached
        position = snapshot.position
        velocity_raw = snapshot.velocity
        statusword = snapshot.statusword
        if cached is None:
            cia402_state = driver_cia402_state_to_api_state(snapshot.cia402_state_raw)
        else:
            cia402_state = driver_cia402_state_to_api_state(cached["cia402_state"])

        return {
            "ts": int(time.time() * 1000),
            "position": self._as_float_or_none(position),
            "velocity": self._as_float_or_none(velocity_raw),
            "torque": None,
            "statusword": statusword,
            "cia402_state": cia402_state.value,
        }

    async def move_to_position(self, req: MoveToPositionRequest, *, op_id: str | None = None) -> dict:
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        motor_lock = self._service.require_motor_lock()
        op_id = self._ensure_op_id(op_id)

        target_pos = req.target_position
        if req.relative:
            try:
                current_pos = await drive.get_position()
            except Exception as exc:
                self._raise_translated("move_to_position", exc)
            target_pos = current_pos + req.target_position

        timeout_s = req.timeout_ms / 1000.0 if req.timeout_ms else 30.0

        async with motor_lock:
            with contextlib.suppress(Exception):
                await drive.jog_stop()

            try:
                await drive.move_to_position(
                    target_position=int(target_pos),
                    velocity=int(req.profile.velocity),
                    accel=int(req.profile.acceleration),
                    decel=int(req.profile.deceleration),
                    timeout_s=timeout_s,
                    require_homing=True,
                    op_id=op_id,
                )
            except MotionAborted:
                return {"target_position": target_pos, "aborted": True}
            except Exception as exc:
                self._raise_translated("move_to_position", exc)

        return {"target_position": target_pos}

    async def jog_start(self, req: JogMoveRequest, *, op_id: str | None = None) -> dict:
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        motor_lock = self._service.require_motor_lock()
        speed = self._resolve_jog_speed(req.speed)
        velocity = speed if req.direction == "positive" else -speed
        op_id = self._ensure_op_id(op_id)

        # Non-queuing: if another motor command holds the lock, return
        # immediately.  Jog is continuous — the next keepalive or a fresh
        # jog_start after the lock is free will set the correct velocity.
        # This prevents 20+ identical requests from queueing and thrashing
        # the motor with redundant Modbus I/O.
        if motor_lock.locked():
            return {"velocity": velocity, "direction": req.direction}

        async with motor_lock:
            try:
                await drive.jog_start(velocity=velocity, ttl_ms=req.ttl_ms, op_id=op_id)
            except Exception as exc:
                self._raise_translated("jog_start", exc)

        return {"velocity": velocity, "direction": req.direction}

    async def jog_update(self, req: JogMoveRequest, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        speed = self._resolve_jog_speed(req.speed)
        velocity = speed if req.direction == "positive" else -speed
        op_id = self._ensure_op_id(op_id)

        try:
            await drive.jog_update(velocity=velocity, ttl_ms=req.ttl_ms, op_id=op_id)
        except Exception as exc:
            self._raise_translated("jog_update", exc)

        return {"velocity": velocity, "direction": req.direction}

    async def jog_stop(self, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        motor_lock = self._service.get_motor_lock_optional()
        op_id = self._ensure_op_id(op_id)

        try:
            if motor_lock is not None:
                async with motor_lock:
                    await drive.jog_stop(op_id=op_id)
            else:
                await drive.jog_stop(op_id=op_id)
        except Exception as exc:
            self._raise_translated("jog_stop", exc)

        return {"stopped": True}

    async def stop(self, req: StopRequest, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        op_id = self._ensure_op_id(op_id)

        try:
            if req.mode == "quick_stop":
                await drive.quick_stop(op_id=op_id)
            else:
                await drive.stop(op_id=op_id)
        except Exception as exc:
            self._raise_translated("stop", exc)

        return {"mode": req.mode}

    async def reference(self, req: ReferenceRequest, *, op_id: str | None = None) -> dict:
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        timeout_s = req.timeout_ms / 1000.0 if req.timeout_ms else 60.0
        op_id = self._ensure_op_id(op_id)

        try:
            result = await drive.home(timeout_s=timeout_s, op_id=op_id)
        except MotionAborted:
            return {"homed": False, "aborted": True}
        except Exception as exc:
            self._raise_translated("reference", exc)

        return {"homed": True, "result": str(result)}

    async def fault_reset(self, req: FaultResetRequest, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        recover = True
        op_id = self._ensure_op_id(op_id)
        if req.after_reset is not None:
            recover = req.after_reset.get("auto_enable", True)

        # Read fault diagnostics before reset (best-effort)
        fault_before = await self._service.read_fault_info()

        try:
            await drive.fault_reset(recover=recover, op_id=op_id)
        except Exception as exc:
            self._raise_translated("fault_reset", exc)

        # Check whether fault actually cleared
        try:
            status_after = await drive.get_status_live()
            fault_cleared = not status_after.get("fault", True)
        except Exception:
            fault_cleared = True  # optimistic if read fails

        # Read new CiA402 state after reset
        new_state: str | None = None
        if fault_cleared:
            try:
                from drivers.dryve_d1.od.statusword import infer_cia402_state

                sw = await drive.read_u16(int(ODIndex.STATUSWORD), 0)
                new_state = driver_cia402_state_to_api_state(infer_cia402_state(sw)).value
            except Exception:
                pass

        return {
            "recovered": recover,
            "fault_cleared": fault_cleared,
            "new_state": new_state,
            "previous_fault": fault_before,
        }
