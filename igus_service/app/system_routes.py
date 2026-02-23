"""System endpoints: /health, /ready, /info, /metrics, root page."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from app import config as app_config
from app.domain.health import (
    HealthWeights,
    compute_drive_health,
    decide_readiness,
    resolve_weights,
)
from app.http_errors import is_drive_connected
from app.version import SERVER_VERSION

try:
    from drivers.dryve_d1 import __version__ as driver_version
except ImportError:
    driver_version = "unknown"

router = APIRouter()

_static_dir = os.path.join(os.path.dirname(__file__), "static")


def _compute_drive_health(app_state: Any):
    drive = getattr(app_state, "drive", None)
    connected = bool(drive is not None and is_drive_connected(drive))
    fault_active = bool(getattr(app_state, "drive_fault_active", False))
    callback_errors_total = int(
        getattr(app_state, "drive_telemetry_callback_errors_total", 0)
    )
    startup_error_present = bool(getattr(app_state, "drive_last_error", None))
    settings = getattr(app_state, "settings", {}) or {}
    telemetry_poll_s = float(settings.get("DRYVE_TELEMETRY_POLL_S", 0.5))
    defaults = HealthWeights(
        disconnected=int(
            getattr(app_config, "DRYVE_HEALTH_WEIGHT_DISCONNECTED", 50)
        ),
        startup_error=int(
            getattr(app_config, "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR", 30)
        ),
        telemetry_stale=int(
            getattr(app_config, "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE", 20)
        ),
        fault_active=int(
            getattr(app_config, "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE", 30)
        ),
        callback_error_max=int(
            getattr(app_config, "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX", 20)
        ),
    )
    weights = resolve_weights(settings, defaults)
    return compute_drive_health(
        connected=connected,
        fault_active=fault_active,
        callback_errors_total=callback_errors_total,
        startup_error_present=startup_error_present,
        telemetry_poll_s=telemetry_poll_s,
        last_telemetry_monotonic=getattr(
            app_state, "drive_last_telemetry_monotonic", None
        ),
        weights=weights,
        now_monotonic=time.monotonic(),
    )


@router.get("/")
async def root() -> Any:
    """Redirect to control panel."""
    control_panel_path = os.path.join(_static_dir, "control_panel.html")
    if os.path.exists(control_panel_path):
        return FileResponse(control_panel_path)
    return {
        "message": "Control panel not found. Please check /docs for API documentation."
    }


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check — process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness check — fail-closed when drive is disconnected or degraded."""
    last_error = getattr(request.app.state, "drive_last_error", None)
    hlth = _compute_drive_health(request.app.state)
    decision = decide_readiness(hlth)
    settings = getattr(request.app.state, "settings", {}) or {}
    response.status_code = decision.http_status

    return {
        "status": decision.status,
        "driver_connected": hlth.connected == 1,
        "code": decision.code,
        "health": {
            "degraded": bool(hlth.degraded),
            "score": hlth.health_score,
            "telemetry_stale": bool(hlth.telemetry_stale),
            "telemetry_age_seconds": round(float(hlth.telemetry_age), 3),
            "fault_active": bool(hlth.fault_active),
            "startup_error_present": bool(hlth.startup_error_present),
            "telemetry_callback_errors_total": hlth.callback_errors_total,
        },
        "drive": {
            "host": settings.get("DRYVE_HOST"),
            "port": settings.get("DRYVE_PORT"),
            "unit_id": settings.get("DRYVE_UNIT_ID"),
            "driver_version": settings.get("DRIVER_VERSION"),
        },
        "last_error": last_error,
    }


@router.get("/info")
async def info() -> dict[str, str]:
    """Server and driver version information."""
    return {
        "server_version": SERVER_VERSION,
        "driver_version": driver_version,
        "protocol": "CiA402",
        "build": "production",
    }


def _build_drive_metrics_body(hlth: Any, *, latest_trace: Any = None) -> str:
    """Build Prometheus-formatted drive health metrics."""
    lines: list[str] = []

    def _gauge(name: str, help_text: str, value: int | float, fmt: str = "d") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value:{fmt}}")

    def _counter(name: str, help_text: str, value: int) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")

    _gauge("igus_drive_connected", "Drive connectivity state (1=connected, 0=disconnected)", int(hlth.connected))
    _gauge("igus_drive_last_telemetry_age_seconds", "Seconds since last telemetry snapshot (-1 means no data yet)", float(hlth.telemetry_age), ".3f")
    _gauge("igus_drive_telemetry_stale", "Drive telemetry freshness (1=stale, 0=fresh)", int(hlth.telemetry_stale))
    _gauge("igus_drive_telemetry_stale_threshold_seconds", "Freshness threshold for telemetry staleness detection", float(hlth.stale_threshold_s), ".3f")
    _gauge("igus_drive_fault_active", "Drive fault bit from latest telemetry snapshot (1=fault, 0=no fault)", int(hlth.fault_active))
    _gauge("igus_drive_startup_error_present", "Startup/connect error presence flag (1=error present, 0=no error)", int(hlth.startup_error_present))
    _gauge("igus_drive_degraded", "Aggregated degradation flag derived from health signals (1=degraded, 0=healthy)", int(hlth.degraded))
    _gauge("igus_drive_health_score", "Aggregated drive health score from 0 (worst) to 100 (best)", int(hlth.health_score))
    _counter("igus_drive_telemetry_callback_errors_total", "Total exceptions in telemetry callback processing", int(hlth.callback_errors_total))

    latest_trace_present = int(latest_trace is not None)
    latest_trace_age_s = -1.0
    if isinstance(latest_trace, dict):
        trace_ts = latest_trace.get("ts")
        if isinstance(trace_ts, int | float):
            latest_trace_age_s = max(0.0, (time.time() * 1000.0 - float(trace_ts)) / 1000.0)

    _gauge("igus_drive_latest_command_trace_present", "Latest command trace snapshot presence (1=present, 0=absent)", latest_trace_present)
    _gauge("igus_drive_latest_command_trace_age_seconds", "Seconds since latest command trace snapshot (-1 means no trace yet)", latest_trace_age_s, ".3f")

    legacy_phase = str(getattr(app_config, "LEGACY_API_PHASE", "deprecated") or "deprecated").lower()
    if legacy_phase not in {"deprecated", "sunset", "removed"}:
        legacy_phase = "deprecated"
    lines.append("# HELP igus_legacy_api_phase Legacy API lifecycle phase gauge by phase label (one active phase has value 1)")
    lines.append("# TYPE igus_legacy_api_phase gauge")
    for phase_name in ("deprecated", "sunset", "removed"):
        phase_value = 1 if legacy_phase == phase_name else 0
        lines.append(f'igus_legacy_api_phase{{phase="{phase_name}"}} {phase_value}')

    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus-compatible metrics export."""
    app_metrics = getattr(request.app.state, "metrics", None)
    body = app_metrics.render_prometheus() if app_metrics else ""
    hlth = _compute_drive_health(request.app.state)
    latest_trace = getattr(request.app.state, "latest_command_trace", None)
    body += _build_drive_metrics_body(hlth, latest_trace=latest_trace)
    return Response(content=body, media_type="text/plain; version=0.0.4")
