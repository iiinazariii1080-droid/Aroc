"""Prometheus metrics endpoint.

Metric objects live in ``app.metrics`` (dependency-free).
This module re-exports them for backward compatibility and provides
the ``GET /metrics`` FastAPI route.
"""
from __future__ import annotations

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter, Depends, Response

from app.core.dependencies import require_api_key

# ── Re-export all metric objects so existing imports of
#    ``from app.routes.metrics import <name>`` keep working. ────────
from app.metrics import (  # noqa: F401
    system_mode,
    recovery_ladder_level,
    stream_active,
    janus_reachable,
    video_age_ms,
    cpu_temp_celsius,
    watchdog_checks_total,
    watchdog_healthy_total,
    watchdog_escalations_total,
    fdir_events_total,
    fdir_events_dropped_total,
    mode_transitions_total,
    ice_connects_total,
    ice_connect_duration_seconds,
    ttff_seconds,
    client_packet_loss_ratio,
    client_frames_decoded,
    client_last_report_ts,
    get_client_state,
    update_client_state,
)

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(require_api_key)])
def prometheus_metrics() -> Response:
    """Return Prometheus text exposition."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
