"""Prometheus ``/metrics`` endpoint.

Metric definitions live in ``app.metrics`` — this module only serves
the HTTP endpoint and re-exports names for backwards compatibility.
"""
from __future__ import annotations

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter, Response

from app.metrics import (  # noqa: F401 — re-export for any remaining consumers
    system_mode,
    recovery_ladder_level,
    stream_active,
    janus_reachable,
    video_age_ms,
    cpu_temp_celsius,
    client_packet_loss_ratio,
    client_frames_decoded_total,
    client_last_report_age_seconds,
    watchdog_checks_total,
    watchdog_healthy_total,
    watchdog_escalations_total,
    fdir_events_total,
    mode_transitions_total,
    ice_connects_total,
    ice_connect_duration_seconds,
    ttff_seconds,
)

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    """Return Prometheus text exposition."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
