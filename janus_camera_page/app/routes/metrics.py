"""Prometheus metrics for the camera stack.

Exposes ``GET /metrics`` in Prometheus text format.

Counters / gauges are updated by the watchdog, recovery ladder, and
system‑mode modules so there is zero per‑request overhead.
"""
from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from fastapi import APIRouter, Response

router = APIRouter(tags=["metrics"])

# ── Gauges (current state) ──────────────────────────────────────────

system_mode = Gauge(
    "camstack_system_mode",
    "Current system mode (0=NOMINAL,1=DEGRADED,2=LOCAL_ONLY,3=SAFE)",
)

recovery_ladder_level = Gauge(
    "camstack_recovery_ladder_level",
    "Current recovery ladder level (0‑4)",
)

stream_active = Gauge(
    "camstack_stream_active",
    "Whether latest watchdog check found active stream (0/1)",
)

janus_reachable = Gauge(
    "camstack_janus_reachable",
    "Whether Janus REST API is reachable (0/1)",
)

video_age_ms = Gauge(
    "camstack_video_age_ms",
    "Age in ms of latest video frame seen by watchdog (-1 if unknown)",
)

cpu_temp_celsius = Gauge(
    "camstack_cpu_temp_celsius",
    "SoC temperature in degrees Celsius (-1 if unavailable)",
)

# ── Counters (cumulative) ───────────────────────────────────────────

watchdog_checks_total = Counter(
    "camstack_watchdog_checks_total",
    "Total watchdog check cycles",
)

watchdog_healthy_total = Counter(
    "camstack_watchdog_healthy_total",
    "Watchdog cycles that found a healthy stream",
)

watchdog_escalations_total = Counter(
    "camstack_watchdog_escalations_total",
    "Recovery ladder escalations triggered by watchdog",
    ["level"],
)

fdir_events_total = Counter(
    "camstack_fdir_events_total",
    "FDIR events emitted",
    ["domain", "severity"],
)

mode_transitions_total = Counter(
    "camstack_mode_transitions_total",
    "System mode transitions",
    ["from_mode", "to_mode"],
)

ice_connects_total = Counter(
    "camstack_ice_connects_total",
    "Client ICE connection events (reported via /telemetry)",
)

# ── Histograms ──────────────────────────────────────────────────────

ice_connect_duration_seconds = Histogram(
    "camstack_ice_connect_duration_seconds",
    "ICE connection time as reported by client getStats()",
    buckets=[0.5, 1, 2, 3, 5, 8, 10, 15, 30],
)


# ── Endpoint ────────────────────────────────────────────────────────

@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    """Return Prometheus text exposition."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
