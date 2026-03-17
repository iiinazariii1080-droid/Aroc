from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

_LOGGER = logging.getLogger(__name__)

from app import error_codes
from app.config import get_settings
from app.application.commands import (
    FaultResetCommand,
    JogCommand,
    MoveCommand,
    ReferenceCommand,
    StopCommand,
)
from app.application.drive_service import DriveService, ServiceError
from app.application.mappers import driver_cia402_state_to_str, mode_display_to_str
from app.application.results import DriveStatusResult, FaultDetailsResult, FaultInfoResult
from drivers.dryve_d1.protocol.exceptions import MotionAborted

if TYPE_CHECKING:
    from app.protocols import AppStateProtocol
    from drivers.dryve_d1.od.statusword import CiA402State as DriverCiA402State


@dataclass(frozen=True)
class _LiveDriveState:
    statusword: int
    position: int | None
    velocity: int | None
    cia402_state_raw: DriverCiA402State
    poll_info: dict[str, Any]
    decoded_status: dict[str, bool] | None = None
    mode_display_raw: int | None = None
    ts_monotonic_s: float | None = None  # populated from telemetry cache; None on live reads


def _try_acquire(lock: asyncio.Lock) -> bool:
    """Non-blocking lock acquisition for asyncio.Lock (or compatible fakes).

    Returns True if the lock was acquired, False if already held.
    This function encapsulates the locked()-check-then-acquire pattern
    so that no await can be inserted between the two operations.

    In asyncio's single-threaded model, ``locked()`` returning False
    guarantees that no other coroutine can acquire the lock before the
    synchronous ``_locked`` flag is set by the C-level ``Lock.acquire()``.

    **CPython dependency**: relies on asyncio's cooperative single-threaded
    scheduling — no context switch can occur between ``locked()`` and the
    synchronous ``coro.send(None)`` call.  On alternative runtimes (PyPy,
    GraalPy) the same invariant holds because ``asyncio.Lock`` is pure-Python
    cooperative, but this should be verified if porting.

    Contention tests: see ``tests/test_concurrent_motion.py``.
    """
    if lock.locked():
        return False
    # Schedule the acquire coroutine but don't await it — we know it will
    # succeed immediately because the lock is not held.  The lock's internal
    # state is set synchronously during the first iteration of acquire().
    #
    # For real asyncio.Lock: acquire() sets _locked=True on the first
    # __next__() call when the lock is free, before yielding control.
    # For test fakes: acquire() is an async def that returns True immediately.
    #
    # We use the low-level protocol to avoid an await here:
    coro = lock.acquire()
    try:
        coro.send(None)
    except StopIteration:
        pass  # coroutine completed synchronously — lock acquired
    else:
        # Coroutine suspended (should not happen for an unlocked lock).
        # Close it to avoid ResourceWarning and fall back to locked() rejection.
        coro.close()
        return False
    return True


class DriveUseCases:
    def __init__(self, app_state: AppStateProtocol) -> None:
        self._service = DriveService(app_state)

    def _raise_translated(self, op: str, exc: Exception) -> None:
        status_code, detail = DriveService.translate_driver_exception(op, exc)
        raise ServiceError(
            status_code=status_code,
            code=str(detail.get("code", "INTERNAL_ERROR")),
            message=str(detail.get("message", f"{op} failed")),
        )

    @staticmethod
    def _ensure_op_id(op_id: str | None) -> str:
        return op_id or uuid.uuid4().hex[:8]

    @staticmethod
    def _as_int_or_none(value) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _resolve_jog_speed(speed: float | None) -> int:
        if speed is None:
            return int(get_settings().dryve_jog_default_speed)
        return int(speed)

    @contextlib.asynccontextmanager
    async def _non_queuing_lock(self):
        """Acquire motor_lock non-queuing — raises MOTOR_BUSY if already held.

        This also serves as an implicit rate limiter for motion endpoints:
        concurrent requests receive MOTOR_BUSY (HTTP 409) immediately via
        ``_try_acquire()``, preventing command flooding against the physical
        actuator without requiring explicit rate-limiting middleware.

        Structurally enforced via ``_try_acquire()``: locked() check and
        acquire() are encapsulated in one helper so the invariant cannot
        be accidentally broken by inserting an await between them.
        """
        motor_lock = self._service.require_motor_lock()
        if not _try_acquire(motor_lock):
            raise ServiceError(409, error_codes.MOTOR_BUSY.code, error_codes.MOTOR_BUSY.message)
        try:
            yield
        finally:
            motor_lock.release()

    async def _read_drive_state(self, *, op: str) -> _LiveDriveState:
        drive = self._service.get_drive()
        _snap = drive.telemetry_latest()
        poll_info: dict[str, Any] = drive.telemetry_poll_info()

        if _snap is not None:
            # Cache path: build snapshot directly from telemetry object — no dict copy.
            return _LiveDriveState(
                statusword=_snap.statusword,
                position=_snap.position,
                velocity=_snap.velocity,
                cia402_state_raw=_snap.cia402_state,
                poll_info=poll_info,
                decoded_status=_snap.decoded_status,
                mode_display_raw=_snap.mode_display,
                ts_monotonic_s=_snap.ts_monotonic_s,
            )

        # Live read path: telemetry poller not yet warmed up or drive just reconnected.
        try:
            statusword = await drive.get_statusword()
            position = await drive.get_position()
            velocity_raw = await drive.get_velocity_actual()
            cia402_state_raw = await drive.get_cia402_state()

            return _LiveDriveState(
                statusword=statusword,
                position=position,
                velocity=velocity_raw,
                cia402_state_raw=cia402_state_raw,
                poll_info=poll_info,
                # decoded_status, mode_display_raw, ts_monotonic_s remain None;
                # _status_fields() will issue live reads for the missing fields.
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

    async def _status_fields(
        self, snapshot: _LiveDriveState,
    ) -> tuple[dict[str, bool], str, str]:
        """Return (status_bits, mode_display_str, cia402_state_str).

        String values correspond to the ``.value`` of their respective API
        enums (``OperationMode``, ``CiA402State``) so the presentation layer
        can reconstruct enums from them without use_cases knowing the types.
        """
        drive = self._service.get_drive()
        status_dict = snapshot.decoded_status
        mode_display_raw = snapshot.mode_display_raw

        if status_dict is None:
            status_dict = await drive.get_status()
        if mode_display_raw is None:
            mode_display_raw = await drive.get_mode_display()

        # cia402_state_raw is always populated (both cache and live paths), so no branch needed.
        cia402_state_str = driver_cia402_state_to_str(snapshot.cia402_state_raw)

        mode_str = mode_display_to_str(mode_display_raw)
        return status_dict or {}, mode_str, cia402_state_str

    async def get_drive_status(self) -> DriveStatusResult:
        drive = self._service.get_drive()

        # Offline + no cache: raise DRIVE_OFFLINE immediately.
        # Without this guard, _read_drive_state would attempt a live Modbus read,
        # fail, and raise STATUS_READ_FAILED — a different error code than the
        # cached-offline path.  Both offline branches now surface DRIVE_OFFLINE.
        if not drive.is_connected and drive.telemetry_latest() is None:
            raise ServiceError(
                503,
                error_codes.DRIVE_OFFLINE.code,
                error_codes.DRIVE_OFFLINE.message,
            )

        snapshot = await self._read_drive_state(op="status")
        poll_info = snapshot.poll_info

        if not drive.is_connected:
            online_str = "offline"
        elif snapshot.ts_monotonic_s is None:
            online_str = "degraded"
        else:
            online_str = "online"

        statusword = snapshot.statusword
        position = snapshot.position
        velocity_raw = snapshot.velocity
        status_dict, mode_str, cia402_state_str = await self._status_fields(snapshot)

        has_fault = status_dict.get("fault", False)
        fault_details_result: FaultDetailsResult | None = None
        if has_fault:
            fault_details_result = await self._service.read_fault_info()
        fault_info_result = FaultInfoResult(active=has_fault, details=fault_details_result)

        poll_period_ms = None
        last_poll_ts = None
        if snapshot.ts_monotonic_s is not None:
            poll_period_ms = poll_info.get("interval_s", 0.5) * 1000
            # Convert the monotonic poll timestamp to a wall-clock Unix ms value.
            _mono_to_wall = time.time() - time.monotonic()
            last_poll_ts = int((_mono_to_wall + snapshot.ts_monotonic_s) * 1000)

        # Read is_moving and is_homed atomically within this call so that callers
        # (e.g. the legacy /status route) don't need to make separate driver calls
        # after the fact — eliminating the TOCTOU window between them.
        is_moving = True  # fail-safe default: unknown state → assume moving to prevent
        is_homed = False  # unsafe follow-on commands if the axis is actually in motion
        try:
            is_moving = await drive.is_moving()
            is_homed = await drive.is_homed()
        except Exception as _motion_exc:
            # Log and keep fail-safe values: returning is_moving=False when the axis
            # might be moving would allow a caller to issue a follow-on motion command
            # while the drive is still in motion — a physical safety hazard.
            _LOGGER.warning(
                "is_moving/is_homed read failed (%s: %s) — reporting is_moving=True (fail-safe)",
                type(_motion_exc).__name__,
                _motion_exc,
            )

        return DriveStatusResult(
            online=online_str,
            connected=drive.is_connected,
            last_poll_ts=last_poll_ts,
            poll_period_ms=poll_period_ms,
            cia402_state=cia402_state_str,
            mode_display=mode_str,
            statusword=statusword,
            status_bits=status_dict,
            remote=status_dict.get("remote"),
            enabled=status_dict.get("operation_enabled"),
            position=self._as_int_or_none(position),
            velocity=self._as_int_or_none(velocity_raw),
            fault=fault_info_result,
            is_moving=is_moving,
            is_homed=is_homed,
        )

    async def get_is_moving(self) -> bool:
        """Return whether the drive is currently in motion.

        Fail-safe: returns True on any error (assume moving) to prevent unsafe
        follow-on commands when motion state cannot be determined.
        """
        try:
            drive = self._service.get_drive(require_connected=True)
            return await drive.is_moving()
        except Exception:
            _LOGGER.warning("is_moving read failed, assuming moving (fail-safe)", exc_info=True)
            return True  # fail-safe: unknown state → assume moving

    async def get_drive_telemetry(self) -> dict[str, Any]:
        snapshot = await self._read_drive_state(op="telemetry")
        position = snapshot.position
        velocity_raw = snapshot.velocity
        statusword = snapshot.statusword
        # cia402_state_raw is always populated regardless of cache vs live path.
        cia402_state_str = driver_cia402_state_to_str(snapshot.cia402_state_raw)

        return {
            "ts": int(time.time() * 1000),
            "position": self._as_int_or_none(position),
            "velocity": self._as_int_or_none(velocity_raw),
            "statusword": statusword,
            "cia402_state": cia402_state_str,
        }

    async def move_to_position(self, cmd: MoveCommand, *, op_id: str | None = None) -> dict:
        # Known limitation: fault check happens before motor_lock acquisition.
        # A fault that occurs between here and the motion command (narrow window while waiting
        # for the lock) will not be caught until the driver's own CiA 402 state check runs.
        # Accepted: the window is at most the wait time for the previous command; the driver
        # itself validates drive state before executing motion.
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        motor_lock = self._service.require_motor_lock()
        op_id = self._ensure_op_id(op_id)
        timeout_s = cmd.timeout_ms / 1000.0

        async with motor_lock:
            # Resolve relative position inside the lock to prevent TOCTOU:
            # two concurrent relative moves must each read the actual position
            # after the previous command has completed.
            target_pos = cmd.target_position
            if cmd.relative:
                try:
                    current_pos = await drive.get_position()
                except Exception as exc:
                    self._raise_translated("move_to_position", exc)
                target_pos = current_pos + cmd.target_position

            try:
                await drive.jog_stop()
            except Exception as _jog_exc:
                _LOGGER.debug("jog_stop pre-move failed (non-fatal): %s", _jog_exc)

            try:
                await drive.move_to_position(
                    target_position=target_pos,
                    velocity=cmd.profile.velocity,
                    accel=cmd.profile.acceleration,
                    decel=cmd.profile.deceleration,
                    timeout_s=timeout_s,
                    require_homing=True,
                    op_id=op_id,
                )
            except MotionAborted:
                return {"target_position": target_pos, "aborted": True}
            except Exception as exc:
                self._raise_translated("move_to_position", exc)

        return {"target_position": target_pos}

    async def jog_start(self, cmd: JogCommand, *, op_id: str | None = None) -> dict:
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        speed = self._resolve_jog_speed(cmd.speed)
        velocity = speed if cmd.direction == "positive" else -speed
        op_id = self._ensure_op_id(op_id)

        # Hot/warm paths: skip motor_lock — the driver's hot/warm paths
        # don't perform CiA402 transitions, only Modbus I/O to set velocity.
        is_hot = drive.is_jog_active()
        is_warm = not is_hot and await drive.is_jog_warm()

        if is_hot or is_warm:
            try:
                await drive.jog_start(velocity=velocity, ttl_ms=cmd.ttl_ms, op_id=op_id)
            except Exception as exc:
                self._raise_translated("jog_start", exc)
            return {"velocity": velocity, "direction": cmd.direction}

        # Cold path: acquire motor_lock (non-queuing).
        async with self._non_queuing_lock():
            try:
                await drive.jog_start(velocity=velocity, ttl_ms=cmd.ttl_ms, op_id=op_id)
            except Exception as exc:
                self._raise_translated("jog_start", exc)

        return {"velocity": velocity, "direction": cmd.direction}

    async def jog_update(self, cmd: JogCommand, *, op_id: str | None = None) -> dict:
        """Refresh jog watchdog TTL and optionally update velocity.

        No motor_lock: jog_update is a lightweight TTL refresh + optional
        velocity change. It only writes to Modbus when jog is already active
        (PV mode), so it cannot conflict with CiA402 state transitions.
        Like jog_stop, it must remain responsive even during cold-path init.
        """
        drive = self._service.get_drive(require_connected=True)
        speed = self._resolve_jog_speed(cmd.speed)
        velocity = speed if cmd.direction == "positive" else -speed
        op_id = self._ensure_op_id(op_id)

        try:
            await drive.jog_update(velocity=velocity, ttl_ms=cmd.ttl_ms, op_id=op_id)
        except Exception as exc:
            self._raise_translated("jog_update", exc)

        return {"velocity": velocity, "direction": cmd.direction}

    async def jog_stop(self, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        op_id = self._ensure_op_id(op_id)

        # Do NOT hold motor_lock here.  Stop commands must be immediately
        # executable regardless of whether another motion command holds the lock.
        # The driver's jog_stop() is interrupt-safe and idempotent (no-op when
        # no jog is active), so it requires no application-level lock.
        try:
            await drive.jog_stop(op_id=op_id)
        except Exception as exc:
            self._raise_translated("jog_stop", exc)

        return {"stopped": True}

    async def stop(self, cmd: StopCommand, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        op_id = self._ensure_op_id(op_id)

        try:
            if cmd.mode == "quick_stop":
                await drive.quick_stop(op_id=op_id)
            else:
                await drive.stop(op_id=op_id)
        except Exception as exc:
            self._raise_translated("stop", exc)

        return {"mode": cmd.mode}

    async def reference(self, cmd: ReferenceCommand, *, op_id: str | None = None) -> dict:
        """Execute homing sequence (non-queuing).

        Uses _non_queuing_lock: if the motor lock is already held by another
        command (e.g. move_to_position), this raises MOTOR_BUSY (409) immediately
        rather than waiting.  Clients that need to home after a move must wait
        for the move to complete before issuing this command.
        """
        await self._service.require_not_in_fault()
        drive = self._service.get_drive(require_connected=True)
        timeout_s = cmd.timeout_ms / 1000.0
        op_id = self._ensure_op_id(op_id)

        async with self._non_queuing_lock():
            try:
                result = await drive.home(timeout_s=timeout_s, op_id=op_id)
            except MotionAborted:
                return {"homed": False, "aborted": True}
            except Exception as exc:
                self._raise_translated("reference", exc)

        return {"homed": True, "result": str(result)}

    async def fault_reset(self, cmd: FaultResetCommand, *, op_id: str | None = None) -> dict:
        drive = self._service.get_drive(require_connected=True)
        recover = cmd.auto_enable
        op_id = self._ensure_op_id(op_id)

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
            fault_cleared = None  # unknown — don't lie to the caller

        # Read new CiA402 state after reset
        new_state: str | None = None
        if fault_cleared:
            try:
                new_state = driver_cia402_state_to_str(await drive.get_cia402_state())
            except Exception:
                pass

        return {
            "auto_enable_requested": recover,
            "fault_cleared": fault_cleared,
            "new_state": new_state,
            "previous_fault": asdict(fault_before) if fault_before is not None else None,
        }
