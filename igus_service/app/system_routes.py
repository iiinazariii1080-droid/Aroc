"""System endpoints: /health, /ready, /info, /metrics, root page."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from app.config import get_legacy_api_phase, get_settings
from app.domain.health import (
    HealthWeights,
    compute_drive_health,
    decide_readiness,
)
from app.application.drive_service import is_drive_connected
from app.version import DRIVER_VERSION as driver_version
from app.version import SERVER_VERSION

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")



def _compute_drive_health(app_state: Any):
    from app.config import Settings
    settings_obj: Settings | None = getattr(app_state, "settings", None)
    s = settings_obj if settings_obj is not None else get_settings()
    drive = getattr(app_state, "drive", None)
    connected = bool(drive is not None and is_drive_connected(drive))
    fault_active = bool(getattr(app_state, "drive_fault_active", False))
    callback_errors_total = int(
        getattr(app_state, "drive_telemetry_callback_errors_total", 0)
    )
    startup_error_present = bool(getattr(app_state, "drive_last_error", None))
    weights = HealthWeights(
        disconnected=s.dryve_health_weight_disconnected,
        startup_error=s.dryve_health_weight_startup_error,
        telemetry_stale=s.dryve_health_weight_telemetry_stale,
        fault_active=s.dryve_health_weight_fault_active,
        callback_error_max=s.dryve_health_weight_callback_error_max,
    )
    return compute_drive_health(
        connected=connected,
        fault_active=fault_active,
        callback_errors_total=callback_errors_total,
        startup_error_present=startup_error_present,
        telemetry_poll_s=s.dryve_telemetry_poll_s,
        last_telemetry_monotonic=getattr(
            app_state, "drive_last_telemetry_monotonic", None
        ),
        weights=weights,
        now_monotonic=time.monotonic(),
        readiness_threshold=s.dryve_health_readiness_threshold,
    )


@router.get("/")
async def root() -> Any:
    """Redirect to control panel."""
    control_panel_path = os.path.join(STATIC_DIR, "control_panel.html")
    if os.path.exists(control_panel_path):
        return FileResponse(control_panel_path)
    return {
        "message": "Control panel not found. Please check /docs for API documentation."
    }


@router.get("/healthz")
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
    from app.config import Settings
    settings_obj = getattr(request.app.state, "settings", None)
    s = settings_obj if isinstance(settings_obj, Settings) else get_settings()
    response.status_code = decision.http_status

    return {
        "status": decision.status,
        "driver_connected": hlth.connected,
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
            "host": s.dryve_host,
            "port": s.dryve_port,
            "unit_id": s.dryve_unit_id,
            "driver_version": driver_version,
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
        "build": get_settings().build_profile,
    }


def _build_drive_metrics_body(
    hlth: Any,
    *,
    latest_trace: Any = None,
    legacy_phase: str | None = None,
    event_bus: Any = None,
) -> str:
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

    # EventBus subscriber metrics
    if event_bus is not None:
        _gauge("igus_sse_subscribers_active", "Number of active SSE subscriber queues", getattr(event_bus, "subscriber_count", 0))
        _counter("igus_sse_subscribers_dropped_total", "Total SSE subscribers dropped due to full queues", int(getattr(event_bus, "subscribers_dropped_total", 0)))

    latest_trace_present = int(latest_trace is not None)
    latest_trace_age_s = -1.0
    if isinstance(latest_trace, dict):
        trace_ts = latest_trace.get("ts")
        if isinstance(trace_ts, int | float):
            latest_trace_age_s = max(0.0, (time.time() * 1000.0 - float(trace_ts)) / 1000.0)

    _gauge("igus_drive_latest_command_trace_present", "Latest command trace snapshot presence (1=present, 0=absent)", latest_trace_present)
    _gauge("igus_drive_latest_command_trace_age_seconds", "Seconds since latest command trace snapshot (-1 means no trace yet)", latest_trace_age_s, ".3f")

    _phase = legacy_phase or get_legacy_api_phase()
    lines.append("# HELP igus_legacy_api_phase Legacy API lifecycle phase gauge by phase label (one active phase has value 1)")
    lines.append("# TYPE igus_legacy_api_phase gauge")
    for phase_name in ("deprecated", "sunset", "removed"):
        phase_value = 1 if _phase == phase_name else 0
        lines.append(f'igus_legacy_api_phase{{phase="{phase_name}"}} {phase_value}')

    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus-compatible metrics export."""
    app_metrics = getattr(request.app.state, "metrics", None)
    body = app_metrics.render_prometheus() if app_metrics else ""
    hlth = _compute_drive_health(request.app.state)
    latest_trace = getattr(request.app.state, "latest_command_trace", None)
    legacy_phase = getattr(request.app.state, "legacy_api_phase", None)
    event_bus = getattr(request.app.state, "event_bus", None)
    body += _build_drive_metrics_body(hlth, latest_trace=latest_trace, legacy_phase=legacy_phase, event_bus=event_bus)
    return Response(content=body, media_type="text/plain; version=0.0.4")
