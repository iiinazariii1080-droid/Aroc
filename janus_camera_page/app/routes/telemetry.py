"""Client WebRTC telemetry ingestion endpoint.

Browsers call ``POST /telemetry`` with ``getStats()`` summaries so
the server can track ICE connection times, candidate types, and
packet-loss rates.  Data feeds Prometheus counters/histograms.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger("telemetry")

router = APIRouter(tags=["telemetry"])


# ── Request models ──────────────────────────────────────────────────

class IceCandidate(BaseModel):
    type: Optional[str] = None            # host | srflx | relay | prflx
    protocol: Optional[str] = None        # udp | tcp
    address: Optional[str] = None
    port: Optional[int] = None


class TelemetryPayload(BaseModel):
    """Subset of RTCPeerConnection.getStats() sent by the player."""
    event: Literal["ice_connected", "ice_failed", "stats_report"] = "stats_report"
    session_id: Optional[str] = None
    camera: Optional[str] = None          # "color" or "depth"
    ice_connect_ms: Optional[float] = None
    time_to_first_frame_ms: Optional[float] = None
    local_candidate: Optional[IceCandidate] = None
    remote_candidate: Optional[IceCandidate] = None
    packets_received: Optional[int] = None
    packets_lost: Optional[int] = None
    jitter: Optional[float] = None
    bytes_received: Optional[int] = None
    frames_decoded: Optional[int] = None
    frames_dropped: Optional[int] = None
    current_rtt: Optional[float] = None
    extra: Optional[Dict[str, Any]] = None


# ── Endpoint ────────────────────────────────────────────────────────

@router.post("/telemetry", status_code=204, response_class=Response, summary="Ingest client WebRTC telemetry")
async def ingest_telemetry(payload: TelemetryPayload, request: Request) -> Response:
    """Accept a telemetry report from the browser player.

    Data is logged as JSON and pushed into Prometheus metrics.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "telemetry from=%s camera=%s event=%s ice_ms=%s ttff_ms=%s",
        client_ip,
        payload.camera,
        payload.event,
        payload.ice_connect_ms,
        payload.time_to_first_frame_ms,
    )

    # ── Prometheus metrics ──
    try:
        from app.routes.metrics import (
            ice_connects_total,
            ice_connect_duration_seconds,
            ttff_seconds,
            client_packet_loss_ratio,
            client_frames_decoded_total,
            client_last_report_age_seconds,
        )

        if payload.event == "ice_connected":
            ice_connects_total.inc()
            if payload.ice_connect_ms is not None:
                ice_connect_duration_seconds.observe(payload.ice_connect_ms / 1000.0)
            if payload.time_to_first_frame_ms is not None:
                ttff_seconds.observe(payload.time_to_first_frame_ms / 1000.0)

        if payload.event == "stats_report":
            client_last_report_age_seconds.set(0)  # reset on each report
            if payload.frames_decoded is not None:
                client_frames_decoded_total.set(payload.frames_decoded)
            if payload.packets_lost is not None and payload.packets_received:
                total = (payload.packets_lost or 0) + payload.packets_received
                if total > 0:
                    client_packet_loss_ratio.set(payload.packets_lost / total)

    except Exception:
        pass  # metrics not available — non-fatal

    return Response(status_code=204)
