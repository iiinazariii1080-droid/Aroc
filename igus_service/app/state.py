from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import FastAPI

from drivers.dryve_d1 import DryveD1
from drivers.dryve_d1 import __version__ as driver_version
from drivers.dryve_d1.api.drive import DryveD1Config
from drivers.dryve_d1.config.models import (
    ConnectionConfig,
    DriveConfig,
    JogConfig,
    MotionLimits,
    PollRates,
    RetryPolicy,
)

from . import config as app_config
from .events import EventBus, EventType

_LOGGER = logging.getLogger(__name__)


def _cfg(name: str, default: Any) -> Any:
    return getattr(app_config, name, default)


async def startup(app: FastAPI) -> None:
    """Initialize application state and connect to the drive.

    Design notes:
    - Exactly one DryveD1 instance per process.
    - The driver already runs an internal telemetry poller (M1).
      We attach a callback and publish high-level events via our EventBus.
    - Motion commands are serialized by an app-level lock.
    """
    app.state.motor_lock = asyncio.Lock()
    app.state.event_bus = EventBus()
    app.state.latest_command_trace = None

    # Connection parameters (defaults aligned with vendor docs; override via env).
    host = _cfg("DRYVE_HOST", "82.165.177.194")
    port = int(_cfg("DRYVE_PORT", 502))
    unit_id = int(_cfg("DRYVE_UNIT_ID", 1))

    connection = ConnectionConfig(
        host=host,
        port=port,
        unit_id=unit_id,
        connect_timeout_s=float(_cfg("DRYVE_CONNECT_TIMEOUT_S", 3.0)),
        request_timeout_s=float(_cfg("DRYVE_REQUEST_TIMEOUT_S", 1.5)),
        socket_idle_timeout_s=float(_cfg("DRYVE_SOCKET_IDLE_TIMEOUT_S", 10.0)),
    )

    retry_max_attempts = _cfg("DRYVE_RETRY_MAX_ATTEMPTS", None)
    retry = RetryPolicy(
        max_attempts=None if retry_max_attempts is None else int(retry_max_attempts),
        base_delay_s=float(_cfg("DRYVE_RETRY_BASE_DELAY_S", 0.25)),
        max_delay_s=float(_cfg("DRYVE_RETRY_MAX_DELAY_S", 5.0)),
        jitter_s=float(_cfg("DRYVE_RETRY_JITTER_S", 0.1)),
    )

    poll = PollRates(
        telemetry_poll_s=float(_cfg("DRYVE_TELEMETRY_POLL_S", 0.5)),
        status_poll_s=float(_cfg("DRYVE_STATUS_POLL_S", 0.25)),
        keepalive_interval_s=float(_cfg("DRYVE_KEEPALIVE_INTERVAL_S", 1.0)),
        keepalive_miss_limit=int(_cfg("DRYVE_KEEPALIVE_MISS_LIMIT", 3)),
    )

    limits = MotionLimits(
        max_abs_position=_cfg("DRYVE_MAX_ABS_POSITION", None),
        max_abs_velocity=_cfg("DRYVE_MAX_ABS_VELOCITY", None),
        max_abs_accel=_cfg("DRYVE_MAX_ABS_ACCEL", None),
        max_abs_decel=_cfg("DRYVE_MAX_ABS_DECEL", None),
        min_position_limit=int(_cfg("DRYVE_MIN_POSITION_LIMIT", 0)),
        max_position_limit=int(_cfg("DRYVE_MAX_POSITION_LIMIT", 120000)),
    )

    jog = JogConfig(ttl_ms=int(_cfg("DRYVE_JOG_TTL_MS", 200)))

    drive_cfg = DriveConfig(connection=connection, retry=retry, poll=poll, limits=limits, jog=jog)
    dryve_cfg = DryveD1Config(drive=drive_cfg)

    # Expose settings for /info and debugging.
    app.state.settings = {
        "DRYVE_HOST": host,
        "DRYVE_PORT": port,
        "DRYVE_UNIT_ID": unit_id,
        "DRIVER_VERSION": driver_version,
        "DRYVE_TELEMETRY_POLL_S": float(_cfg("DRYVE_TELEMETRY_POLL_S", 0.5)),
        "DRYVE_HEALTH_WEIGHT_DISCONNECTED": int(_cfg("DRYVE_HEALTH_WEIGHT_DISCONNECTED", 50)),
        "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR": int(_cfg("DRYVE_HEALTH_WEIGHT_STARTUP_ERROR", 30)),
        "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE": int(_cfg("DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE", 20)),
        "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE": int(_cfg("DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE", 30)),
        "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX": int(_cfg("DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX", 20)),
    }

    # Event publishing callback (invoked by driver's telemetry poller task).
    event_bus: EventBus = app.state.event_bus
    loop = asyncio.get_running_loop()

    prev_state: dict[str, object] = {"cia402_state": None, "fault": None}
    last_status_emit_s = 0.0
    status_emit_period_s = 0.5  # keep SSE/UI responsive without flooding
    _background_tasks: set[asyncio.Task[None]] = set()

    def _fire_and_forget(coro: Any) -> None:
        """Schedule a coroutine and prevent GC before completion."""
        task = loop.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    def on_snapshot(snapshot) -> None:
        nonlocal last_status_emit_s
        try:
            app.state.drive_last_telemetry_monotonic = time.monotonic()
            cia_state = snapshot.cia402_state
            fault = bool(snapshot.decoded_status.get("fault", False)) if snapshot.decoded_status else False
            app.state.drive_fault_active = fault

            # State change
            if prev_state["cia402_state"] is not None and prev_state["cia402_state"] != cia_state:
                _fire_and_forget(
                    event_bus.publish(
                        EventType.STATE_CHANGE,
                        {
                            "from_state": str(prev_state["cia402_state"]),
                            "to_state": str(cia_state),
                            "statusword": snapshot.statusword,
                        },
                    )
                )

            # Fault edge
            if prev_state["fault"] is not None and prev_state["fault"] != fault:
                _fire_and_forget(
                    event_bus.publish(
                        EventType.FAULT,
                        {
                            "active": fault,
                            "statusword": snapshot.statusword,
                        },
                    )
                )

            prev_state["cia402_state"] = cia_state
            prev_state["fault"] = fault

            # Periodic STATUS (throttled)
            now = time.monotonic()
            if now - last_status_emit_s >= status_emit_period_s:
                last_status_emit_s = now
                _fire_and_forget(
                    event_bus.publish(
                        EventType.STATUS,
                        {
                            "ts_monotonic_s": snapshot.ts_monotonic_s,
                            "statusword": snapshot.statusword,
                            "cia402_state": str(snapshot.cia402_state),
                            "position": snapshot.position,
                            "velocity": snapshot.velocity,
                            "mode_display": snapshot.mode_display,
                            "decoded_status": snapshot.decoded_status,
                        },
                    )
                )
        except Exception:
            # Never let callback break the poller loop
            app.state.drive_telemetry_callback_errors_total += 1
            _LOGGER.exception("Telemetry snapshot callback error")

    # Initialize the DryveD1 driver
    app.state.drive = None
    app.state.drive_last_error = None
    app.state.drive_last_telemetry_monotonic = None
    app.state.drive_fault_active = False
    app.state.drive_telemetry_callback_errors_total = 0
    try:
        drive = DryveD1(config=dryve_cfg)
        await drive.connect()
        drive.set_telemetry_callback(on_snapshot)

        app.state.drive = drive
        _LOGGER.info(
            "DryveD1 connected host=%s port=%d unit_id=%d driver_version=%s",
            host,
            port,
            unit_id,
            driver_version,
        )
    except Exception as exc:
        app.state.drive = None
        # Provide actionable hint for the most common integration error.
        msg = str(exc)
        try:
            from drivers.dryve_d1.protocol.exceptions import (
                ModbusExceptionCode,
                ModbusGatewayException,
            )
            if isinstance(exc, ModbusGatewayException) and exc.as_enum() == ModbusExceptionCode.ILLEGAL_FUNCTION:
                msg = (
                    f"{exc} — the remote Modbus server rejected function 0x2B (dryve D1 Modbus TCP Gateway). "
                    f"This usually means you are connected to the wrong port/service, or the Modbus TCP Gateway is not enabled. "
                    f"For the project simulator use port 501 (not 502)."
                )
        except Exception:
            pass
        app.state.drive_last_error = msg
        _LOGGER.exception("Failed to initialize DryveD1: %s", msg)


async def shutdown(app: FastAPI) -> None:
    """Gracefully stop background components and release resources."""
    drive = getattr(app.state, "drive", None)
    if drive is not None:
        with contextlib.suppress(Exception):
            drive.set_telemetry_callback(None)
        try:
            await drive.close()
            _LOGGER.info("DryveD1 driver closed")
        except Exception:
            _LOGGER.exception("Error during DryveD1 shutdown")

    for attr in (
        "drive",
        "event_bus",
        "latest_command_trace",
        "motor_lock",
        "settings",
        "drive_last_error",
        "drive_last_telemetry_monotonic",
        "drive_fault_active",
        "drive_telemetry_callback_errors_total",
    ):
        if hasattr(app.state, attr):
            with contextlib.suppress(Exception):
                delattr(app.state, attr)
