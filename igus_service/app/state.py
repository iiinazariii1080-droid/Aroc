from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import FastAPI

from app.version import DRIVER_VERSION as driver_version
from drivers.dryve_d1 import DryveD1

from .auth import get_api_key, is_auth_disabled
from .config import Settings, create_dryve_config, get_legacy_api_phase, get_settings
from .events import EventBus, EventType

_LOGGER = logging.getLogger(__name__)


class _TelemetryEventProcessor:
    """Processes telemetry snapshots and publishes events via EventBus.

    All methods run exclusively on the event loop thread (scheduled via
    call_soon_threadsafe).  Encapsulates the mutable edge-detection state
    that was previously captured via closure nonlocals in startup().
    """

    __slots__ = (
        "_app", "_event_bus", "_throttle_s", "_error_window_s",
        "_prev_cia_state", "_prev_fault",
        "_last_status_emit_s", "_error_counter_reset_s",
    )

    def __init__(
        self,
        app: FastAPI,
        event_bus: "EventBus",
        throttle_s: float,
        error_window_s: float = 60.0,
    ) -> None:
        self._app = app
        self._event_bus = event_bus
        self._throttle_s = throttle_s
        self._error_window_s = error_window_s
        self._prev_cia_state: object = None
        self._prev_fault: object = None
        self._last_status_emit_s: float = 0.0
        self._error_counter_reset_s: float = 0.0

    def handle(
        self,
        cia_state: Any,
        fault: bool,
        now_monotonic: float,
        snapshot: Any,
    ) -> None:
        """Process one telemetry snapshot (event-loop thread only)."""
        self._app.state.drive_last_telemetry_monotonic = now_monotonic
        self._app.state.drive_fault_active = fault

        # Decay callback-error counter periodically
        if now_monotonic - self._error_counter_reset_s >= self._error_window_s:
            self._error_counter_reset_s = now_monotonic
            self._app.state.drive_telemetry_callback_errors_total = 0

        # State change edge
        if self._prev_cia_state is not None and self._prev_cia_state != cia_state:
            self._event_bus.publish(
                EventType.STATE_CHANGE,
                {
                    "from_state": str(self._prev_cia_state),
                    "to_state": str(cia_state),
                    "statusword": snapshot.statusword,
                },
            )

        # Fault edge
        if self._prev_fault is not None and self._prev_fault != fault:
            self._event_bus.publish(
                EventType.FAULT,
                {
                    "active": fault,
                    "statusword": snapshot.statusword,
                },
            )

        self._prev_cia_state = cia_state
        self._prev_fault = fault

        # Throttled STATUS broadcast
        if now_monotonic - self._last_status_emit_s >= self._throttle_s:
            self._last_status_emit_s = now_monotonic
            self._event_bus.publish(
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

    def inc_callback_errors(self) -> None:
        """Increment the callback error counter (event-loop thread only)."""
        self._app.state.drive_telemetry_callback_errors_total += 1


# Exhaustive list of app.state attributes that startup() must set.
# A mismatch (typo, removal, rename) raises loudly at process start
# rather than silently returning wrong defaults at health-check time.

_REQUIRED_STATE_ATTRS: tuple[str, ...] = (
    "drive",
    "drive_fault_active",
    "drive_last_error",
    "drive_last_telemetry_monotonic",
    "drive_telemetry_callback_errors_total",
    "event_bus",
    "latest_command_trace",
    "legacy_api_phase",
    "motor_lock",
    "settings",
)


def _validate_settings(s: Settings) -> None:
    """Validate critical config values early — fail loudly at startup."""
    if not s.dryve_host:
        raise ValueError("DRYVE_HOST must be set via DRYVE_HOST env var (or IGUS_MOTOR_IP as legacy alias)")
    if not (0 < s.dryve_connect_timeout_s < 60):
        raise ValueError(
            f"DRYVE_CONNECT_TIMEOUT_S out of range (0, 60): {s.dryve_connect_timeout_s}"
        )
    if not (0 < s.dryve_request_timeout_s < 30):
        raise ValueError(
            f"DRYVE_REQUEST_TIMEOUT_S out of range (0, 30): {s.dryve_request_timeout_s}"
        )
    if s.dryve_min_position_limit >= s.dryve_max_position_limit:
        raise ValueError(
            f"DRYVE_MIN_POSITION_LIMIT ({s.dryve_min_position_limit}) "
            f"must be < DRYVE_MAX_POSITION_LIMIT ({s.dryve_max_position_limit})"
        )
    if s.dryve_retry_base_delay_s > s.dryve_retry_max_delay_s:
        raise ValueError(
            f"DRYVE_RETRY_BASE_DELAY_S ({s.dryve_retry_base_delay_s}) "
            f"must be <= DRYVE_RETRY_MAX_DELAY_S ({s.dryve_retry_max_delay_s})"
        )
    if s.dryve_keepalive_interval_s <= 0:
        raise ValueError(
            f"DRYVE_KEEPALIVE_INTERVAL_S must be > 0, got {s.dryve_keepalive_interval_s}"
        )
    if s.dryve_jog_ttl_ms < 0:
        raise ValueError(
            f"DRYVE_JOG_TTL_MS must be >= 0, got {s.dryve_jog_ttl_ms}"
        )
    if not (0 <= s.dryve_health_readiness_threshold <= 100):
        raise ValueError(
            f"DRYVE_HEALTH_READINESS_THRESHOLD must be in [0, 100], got {s.dryve_health_readiness_threshold}"
        )


async def startup(app: FastAPI) -> None:
    """Initialize application state and connect to the drive.

    Design notes:
    - Exactly one DryveD1 instance per process.
    - The driver already runs an internal telemetry poller.
      We attach a callback and publish high-level events via our EventBus.
    - Motion commands are serialized by an app-level lock.
    """
    settings = get_settings()
    _validate_settings(settings)

    # Compute and cache legacy API phase once at startup — middleware reads from
    # app.state instead of calling get_legacy_api_phase() on every request.
    app.state.legacy_api_phase = get_legacy_api_phase()
    app.state.motor_lock = asyncio.Lock()
    app.state.event_bus = EventBus()
    app.state.latest_command_trace = None

    # Warn loudly when auth is disabled — motion commands will be open.
    if get_api_key() is None:
        if is_auth_disabled():
            _LOGGER.warning(
                "IGUS_AUTH_DISABLED=true — authentication is explicitly disabled. "
                "All motion endpoints are open. Do NOT use in production."
            )
        else:
            _LOGGER.warning(
                "IGUS_API_KEY is not set — all motion endpoints are UNPROTECTED. "
                "Set IGUS_API_KEY env var to secure motion commands, or set "
                "IGUS_AUTH_DISABLED=true to suppress this warning in development."
            )

    # Single factory builds the entire driver config from Settings.
    dryve_cfg = create_dryve_config(settings)

    # Expose full Settings object — to_info_dict() is used only for serialization
    # in /info and /ready endpoints, not as the canonical storage format.
    app.state.settings = settings

    # Event publishing callback (invoked by driver's telemetry poller task).
    event_bus: EventBus = app.state.event_bus
    loop = asyncio.get_running_loop()

    processor = _TelemetryEventProcessor(
        app=app,
        event_bus=event_bus,
        throttle_s=settings.dryve_status_event_throttle_s,
    )

    def on_snapshot(snapshot) -> None:
        # Schedule mutable-state updates via call_soon_threadsafe so they
        # execute as a discrete callback on the event loop thread.
        try:
            now_monotonic = time.monotonic()
            cia_state = snapshot.cia402_state
            fault = bool(snapshot.decoded_status.get("fault", False)) if snapshot.decoded_status else False

            loop.call_soon_threadsafe(
                processor.handle, cia_state, fault, now_monotonic, snapshot
            )
        except Exception:
            # Never let the callback break the poller loop.
            loop.call_soon_threadsafe(processor.inc_callback_errors)
            _LOGGER.exception("Telemetry snapshot callback error")

    # Initialize the DryveD1 driver
    app.state.drive = None
    app.state.drive_last_error = None
    app.state.drive_last_telemetry_monotonic = None
    app.state.drive_fault_active = False
    app.state.drive_telemetry_callback_errors_total = 0

    # Guard: all required state attrs must be set by this point.
    # A missing attr indicates a name typo or an incomplete refactor —
    # fail loudly here rather than silently returning wrong defaults later.
    _missing = [a for a in _REQUIRED_STATE_ATTRS if not hasattr(app.state, a)]
    if _missing:
        raise RuntimeError(f"startup() did not set required app.state attributes: {_missing}")
    # Critical infrastructure attrs must never be None — motor_lock and event_bus
    # are created unconditionally before this point; None would mean the startup
    # code was changed incorrectly.
    for _critical_attr in ("motor_lock", "event_bus"):
        if getattr(app.state, _critical_attr, None) is None:
            raise RuntimeError(
                f"startup(): critical attribute {_critical_attr!r} is None — initialization failed"
            )

    try:
        drive = DryveD1(config=dryve_cfg)
        await drive.connect(telemetry_callback=on_snapshot)

        app.state.drive = drive
        _LOGGER.info(
            "DryveD1 connected host=%s port=%d unit_id=%d driver_version=%s",
            settings.dryve_host,
            settings.dryve_port,
            settings.dryve_unit_id,
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
        # Trade-off: drive_last_error is intentionally never cleared after a
        # startup failure.  Health scoring derives `startup_error_present` from
        # this field, applying a permanent penalty until the process restarts.
        app.state.drive_last_error = msg
        _LOGGER.exception("Failed to initialize DryveD1: %s", msg)


async def shutdown(app: FastAPI) -> None:
    """Gracefully stop background components and release resources."""
    # Notify SSE subscribers before tearing down so they close gracefully
    # instead of waiting for a TCP-level disconnect.
    event_bus = getattr(app.state, "event_bus", None)
    if event_bus is not None:
        with contextlib.suppress(Exception):
            event_bus.shutdown_notify()

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
        "legacy_api_phase",
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
