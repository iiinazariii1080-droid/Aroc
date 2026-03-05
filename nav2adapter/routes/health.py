"""
Health probes and reliability metrics endpoints.

Extracted from main.py (НАР-7) to reduce main module size.
"""
import os
import time
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from routes.decorators import safe_getter
from models.api_types import GenericResponse
from services.reliability_metrics import reliability_metrics

_LOGGER = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Health-check thresholds (env-configurable)
# ---------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        _LOGGER.warning("Invalid env %s=%r, using default %s", name, os.getenv(name), default)
        return default

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        _LOGGER.warning("Invalid env %s=%r, using default %s", name, os.getenv(name), default)
        return default

HEARTBEAT_STALE_S = _env_float("HEALTH_HEARTBEAT_STALE_S", 10.0)
HEALTH_EVENTBUS_DROP_MAX = _env_int("HEALTH_EVENTBUS_DROP_MAX", 0)
HEALTH_PERSISTENCE_DROP_MAX = _env_int("HEALTH_PERSISTENCE_DROP_MAX", 0)
HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX = _env_int("HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX", 0)
HEALTH_LATENCY_MIN_SAMPLES = _env_int("HEALTH_LATENCY_MIN_SAMPLES", 10)
HEALTH_MQTT_EVENT_LATENCY_MAX_MS = _env_float("HEALTH_MQTT_EVENT_LATENCY_MAX_MS", 1500.0)
HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS = _env_float("HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS", 35000.0)
HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS = _env_float("HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS", 250.0)
HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S = _env_float("HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S", 0.01)
HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S = _env_float("HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S", 0.005)
HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S = _env_float("HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S", 0.01)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _reliability_health_summary() -> dict:
    snap = reliability_metrics.snapshot()
    counters = snap.get("counters", {}) if isinstance(snap, dict) else {}
    duration = snap.get("duration", {}) if isinstance(snap, dict) else {}
    rates_60s = snap.get("rates_60s", {}) if isinstance(snap, dict) else {}
    degraded_reasons: list[str] = []

    eventbus_drops = int(counters.get("eventbus.publish.drop_oldest", 0) or 0)
    persistence_drops = int(counters.get("persistence.enqueue.drop_queue_full", 0) or 0)
    mqtt_event_failures = int(counters.get("mqtt.publish.event.failure", 0) or 0)

    if eventbus_drops > HEALTH_EVENTBUS_DROP_MAX:
        degraded_reasons.append(
            f"eventbus_drop_oldest:{eventbus_drops}>{HEALTH_EVENTBUS_DROP_MAX}"
        )
    if persistence_drops > HEALTH_PERSISTENCE_DROP_MAX:
        degraded_reasons.append(
            f"persistence_queue_drop:{persistence_drops}>{HEALTH_PERSISTENCE_DROP_MAX}"
        )
    if mqtt_event_failures > HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX:
        degraded_reasons.append(
            f"mqtt_event_publish_failure:{mqtt_event_failures}>{HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX}"
        )

    def _maybe_add_rate_reason(metric_name: str, threshold_per_s: float, reason_key: str) -> None:
        value = float(rates_60s.get(metric_name, 0.0) or 0.0)
        if value > float(threshold_per_s):
            degraded_reasons.append(
                f"{reason_key}:rate_per_s={value:.6f}>{float(threshold_per_s):.6f}"
            )

    _maybe_add_rate_reason(
        "eventbus.publish.drop_oldest",
        HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S,
        "eventbus_drop_rate",
    )
    _maybe_add_rate_reason(
        "persistence.enqueue.drop_queue_full",
        HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S,
        "persistence_drop_rate",
    )
    _maybe_add_rate_reason(
        "mqtt.publish.event.failure",
        HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S,
        "mqtt_event_failure_rate",
    )

    def _duration_stats(name: str) -> tuple[int, float, float]:
        payload = duration.get(name)
        if not isinstance(payload, dict):
            return 0, 0.0, 0.0
        count = int(payload.get("count", 0) or 0)
        sum_s = float(payload.get("sum_s", 0.0) or 0.0)
        max_s = float(payload.get("max_s", 0.0) or 0.0)
        avg_s = (sum_s / count) if count > 0 else 0.0
        return count, avg_s, max_s

    def _maybe_add_latency_reason(metric_name: str, threshold_ms: float, reason_key: str) -> None:
        count, avg_s, max_s = _duration_stats(metric_name)
        if count < max(1, HEALTH_LATENCY_MIN_SAMPLES):
            return
        avg_ms = avg_s * 1000.0
        max_ms = max_s * 1000.0
        if avg_ms > threshold_ms:
            degraded_reasons.append(
                f"{reason_key}:avg_ms={avg_ms:.1f}>{threshold_ms:.1f} (n={count},max_ms={max_ms:.1f})"
            )

    _maybe_add_latency_reason(
        "mqtt.publish.event.latency_s",
        HEALTH_MQTT_EVENT_LATENCY_MAX_MS,
        "mqtt_event_latency",
    )
    _maybe_add_latency_reason(
        "symovo.transport_wait_for_changes.latency_s",
        HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS,
        "symovo_transport_longpoll_latency",
    )
    _maybe_add_latency_reason(
        "symovo.amr_wait_for_changes.latency_s",
        HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS,
        "symovo_amr_longpoll_latency",
    )
    _maybe_add_latency_reason(
        "persistence.writer.upsert.latency_s",
        HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS,
        "persistence_upsert_latency",
    )
    _maybe_add_latency_reason(
        "persistence.writer.upsert_session.latency_s",
        HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS,
        "persistence_upsert_session_latency",
    )
    _maybe_add_latency_reason(
        "persistence.writer.delete.latency_s",
        HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS,
        "persistence_delete_latency",
    )
    _maybe_add_latency_reason(
        "persistence.writer.delete_session.latency_s",
        HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS,
        "persistence_delete_session_latency",
    )

    return {
        "degraded": bool(degraded_reasons),
        "reasons": degraded_reasons,
        "thresholds": {
            "eventbus_drop_max": HEALTH_EVENTBUS_DROP_MAX,
            "persistence_drop_max": HEALTH_PERSISTENCE_DROP_MAX,
            "mqtt_event_publish_fail_max": HEALTH_MQTT_EVENT_PUBLISH_FAIL_MAX,
            "latency_min_samples": HEALTH_LATENCY_MIN_SAMPLES,
            "mqtt_event_latency_max_ms": HEALTH_MQTT_EVENT_LATENCY_MAX_MS,
            "symovo_longpoll_latency_max_ms": HEALTH_SYMOVO_LONGPOLL_LATENCY_MAX_MS,
            "persistence_write_latency_max_ms": HEALTH_PERSISTENCE_WRITE_LATENCY_MAX_MS,
            "eventbus_drop_rate_max_per_s": HEALTH_EVENTBUS_DROP_RATE_MAX_PER_S,
            "persistence_drop_rate_max_per_s": HEALTH_PERSISTENCE_DROP_RATE_MAX_PER_S,
            "mqtt_event_failure_rate_max_per_s": HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S,
        },
        "snapshot": snap,
    }


def _reliability_ops_payload(*, top_n: int = 20) -> dict:
    summary = _reliability_health_summary()
    snapshot = summary.get("snapshot", {}) if isinstance(summary, dict) else {}
    counters = snapshot.get("counters", {}) if isinstance(snapshot, dict) else {}
    duration = snapshot.get("duration", {}) if isinstance(snapshot, dict) else {}
    rates_60s = snapshot.get("rates_60s", {}) if isinstance(snapshot, dict) else {}
    items = sorted(
        ((str(key), int(value)) for key, value in counters.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    top_items = items[: max(1, int(top_n or 20))]

    duration_items = []
    for key, stats in duration.items():
        if not isinstance(stats, dict):
            continue
        count = int(stats.get("count", 0) or 0)
        sum_s = float(stats.get("sum_s", 0.0) or 0.0)
        max_s = float(stats.get("max_s", 0.0) or 0.0)
        avg_s = (sum_s / count) if count > 0 else 0.0
        duration_items.append((str(key), count, avg_s, max_s))
    duration_items.sort(key=lambda row: row[2], reverse=True)
    top_duration = duration_items[: max(1, int(top_n or 20))]

    rate_items = sorted(
        ((str(key), float(value)) for key, value in rates_60s.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    top_rates = rate_items[: max(1, int(top_n or 20))]

    return {
        "status": "ok",
        "reliability": {
            "degraded": bool(summary.get("degraded", False)),
            "reasons": summary.get("reasons", []),
            "thresholds": summary.get("thresholds", {}),
            "uptime_s": snapshot.get("uptime_s", 0.0),
            "counter_total": len(counters),
            "top_counters": [{"name": name, "value": value} for name, value in top_items],
            "duration_total": len(duration_items),
            "top_duration_ms": [
                {
                    "name": name,
                    "count": count,
                    "avg_ms": round(avg_s * 1000.0, 3),
                    "max_ms": round(max_s * 1000.0, 3),
                }
                for name, count, avg_s, max_s in top_duration
            ],
            "rate_total": len(rate_items),
            "top_rates_per_sec": [
                {
                    "name": name,
                    "rate_per_sec": round(rate, 6),
                }
                for name, rate in top_rates
            ],
        },
    }


def _reliability_prometheus_text() -> str:
    """Render reliability snapshot as Prometheus-compatible plain text."""
    summary = _reliability_health_summary()
    snapshot = summary.get("snapshot", {}) if isinstance(summary, dict) else {}
    counters = snapshot.get("counters", {}) if isinstance(snapshot, dict) else {}
    duration = snapshot.get("duration", {}) if isinstance(snapshot, dict) else {}
    rates_60s = snapshot.get("rates_60s", {}) if isinstance(snapshot, dict) else {}

    lines: list[str] = []
    lines.append("# HELP nav2adapter_reliability_degraded 1 if reliability summary is degraded")
    lines.append("# TYPE nav2adapter_reliability_degraded gauge")
    lines.append(f"nav2adapter_reliability_degraded {1 if bool(summary.get('degraded', False)) else 0}")

    lines.append("# HELP nav2adapter_reliability_counter Runtime reliability counters")
    lines.append("# TYPE nav2adapter_reliability_counter gauge")
    for key in sorted(counters.keys()):
        safe_key = str(key).replace('"', "'")
        value = int(counters.get(key, 0) or 0)
        lines.append(f'nav2adapter_reliability_counter{{name="{safe_key}"}} {value}')

    lines.append("# HELP nav2adapter_reliability_rate_per_sec Rolling 60s rate per second")
    lines.append("# TYPE nav2adapter_reliability_rate_per_sec gauge")
    for key in sorted(rates_60s.keys()):
        safe_key = str(key).replace('"', "'")
        value = float(rates_60s.get(key, 0.0) or 0.0)
        lines.append(f'nav2adapter_reliability_rate_per_sec{{name="{safe_key}"}} {value:.9f}')

    lines.append("# HELP nav2adapter_reliability_duration_avg_ms Average duration in ms")
    lines.append("# TYPE nav2adapter_reliability_duration_avg_ms gauge")
    lines.append("# HELP nav2adapter_reliability_duration_max_ms Max duration in ms")
    lines.append("# TYPE nav2adapter_reliability_duration_max_ms gauge")
    for key in sorted(duration.keys()):
        payload = duration.get(key)
        if not isinstance(payload, dict):
            continue
        safe_key = str(key).replace('"', "'")
        count = int(payload.get("count", 0) or 0)
        sum_s = float(payload.get("sum_s", 0.0) or 0.0)
        max_s = float(payload.get("max_s", 0.0) or 0.0)
        avg_ms = (sum_s / count) * 1000.0 if count > 0 else 0.0
        max_ms = max_s * 1000.0
        lines.append(f'nav2adapter_reliability_duration_avg_ms{{name="{safe_key}"}} {avg_ms:.6f}')
        lines.append(f'nav2adapter_reliability_duration_max_ms{{name="{safe_key}"}} {max_ms:.6f}')

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/healthz",
    response_model=GenericResponse,
    summary="Health check",
    description="Liveness/readiness probe for the Symovo API gateway.",
    response_description="OK status",
)
@safe_getter(GenericResponse)
async def healthz(request: Request) -> Any:
    # Backward-compatible alias for /readyz
    return await readyz(request)


@router.get("/livez", response_model=GenericResponse, summary="Liveness probe")
@safe_getter(GenericResponse)
async def livez(request: Request) -> Any:
    app_state = request.app.state
    last = float(getattr(app_state, "last_heartbeat_ts", 0.0) or 0.0)
    now = time.time()
    stale_for = now - last
    if stale_for > HEARTBEAT_STALE_S:
        _LOGGER.warning("Health check /livez: unhealthy (heartbeat_stale=%.1fs)", stale_for)
        raise HTTPException(status_code=503, detail={"error": {"type": "Unhealthy", "msg": f"heartbeat_stale:{stale_for:.1f}s"}})
    _LOGGER.info("Health check /livez OK (heartbeat_stale_s=%.2f)", stale_for)
    return {
        "status": "ok",
        "heartbeat_stale_s": stale_for,
        "reliability": _reliability_health_summary(),
    }


@router.get("/readyz", response_model=GenericResponse, summary="Readiness probe")
@safe_getter(GenericResponse)
async def readyz(request: Request) -> Any:
    app_state = request.app.state
    if not bool(getattr(app_state, "startup_ok", False)):
        _LOGGER.warning("Health check /readyz: not ready (startup_incomplete)")
        raise HTTPException(status_code=503, detail={"error": {"type": "NotReady", "msg": "startup_incomplete"}})

    sp = getattr(app_state, "status_publisher", None)
    sp_running = bool(getattr(sp, "_running", False)) if sp is not None else False
    if not sp_running:
        _LOGGER.warning("Health check /readyz: not ready (status_publisher_not_running)")
        raise HTTPException(status_code=503, detail={"error": {"type": "NotReady", "msg": "status_publisher_not_running"}})

    ma = getattr(app_state, "mqtt_adapter", None)
    if ma is not None:
        connected = bool(getattr(ma, "is_connected", False) or getattr(ma, "_connected", False))
        if not connected:
            # MQTT disconnect is degraded, not a readiness failure.
            # HTTP API is fully functional without MQTT. The reconnect loop
            # will restore MQTT automatically. Failing readiness here would
            # cause K8s to remove the pod, which is worse than degraded mode.
            _LOGGER.warning("Health check /readyz: MQTT disconnected (degraded mode)")

    _LOGGER.info("Health check /readyz OK")
    return {"status": "ok", "reliability": _reliability_health_summary()}


@router.get("/ops/reliabilityz", response_model=GenericResponse, summary="Reliability diagnostics")
@safe_getter(GenericResponse)
async def reliabilityz(top_n: int = 20) -> Any:
    """Operational reliability diagnostics: degraded summary + top counters."""
    return _reliability_ops_payload(top_n=top_n)


@router.get("/ops/reliability.prom", response_class=PlainTextResponse, summary="Reliability metrics (Prometheus text)")
async def reliability_prom() -> PlainTextResponse:
    """Prometheus-friendly reliability metrics for monitoring systems."""
    return PlainTextResponse(content=_reliability_prometheus_text(), media_type="text/plain; version=0.0.4")
