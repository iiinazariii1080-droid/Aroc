from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HealthWeights:
    disconnected: int
    startup_error: int
    telemetry_stale: int
    fault_active: int
    callback_error_max: int


@dataclass(frozen=True)
class DriveHealth:
    connected: int
    fault_active: int
    callback_errors_total: int
    startup_error_present: int
    telemetry_poll_s: float
    stale_threshold_s: float
    telemetry_age: float
    telemetry_stale: int
    degraded: int
    health_score: int


@dataclass(frozen=True)
class ReadinessDecision:
    status: str
    code: str | None
    http_status: int


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def resolve_weights(settings: dict[str, Any], defaults: HealthWeights) -> HealthWeights:
    return HealthWeights(
        disconnected=max(0, _safe_int(settings.get("DRYVE_HEALTH_WEIGHT_DISCONNECTED"), defaults.disconnected)),
        startup_error=max(0, _safe_int(settings.get("DRYVE_HEALTH_WEIGHT_STARTUP_ERROR"), defaults.startup_error)),
        telemetry_stale=max(0, _safe_int(settings.get("DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE"), defaults.telemetry_stale)),
        fault_active=max(0, _safe_int(settings.get("DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE"), defaults.fault_active)),
        callback_error_max=max(0, _safe_int(settings.get("DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX"), defaults.callback_error_max)),
    )


def compute_drive_health(
    *,
    connected: bool,
    fault_active: bool,
    callback_errors_total: int,
    startup_error_present: bool,
    telemetry_poll_s: float,
    last_telemetry_monotonic: float | None,
    weights: HealthWeights,
    now_monotonic: float | None = None,
) -> DriveHealth:
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    poll_s = max(0.05, float(telemetry_poll_s))
    stale_threshold_s = max(2.0, poll_s * 3.0)

    if last_telemetry_monotonic is None:
        telemetry_age = -1.0
    else:
        telemetry_age = max(0.0, now - float(last_telemetry_monotonic))

    telemetry_stale = 1 if (telemetry_age < 0.0 or telemetry_age > stale_threshold_s) else 0
    callback_penalty = min(max(0, int(callback_errors_total)), weights.callback_error_max)

    score = 100
    if not connected:
        score -= weights.disconnected
    if startup_error_present:
        score -= weights.startup_error
    if telemetry_stale == 1:
        score -= weights.telemetry_stale
    if fault_active:
        score -= weights.fault_active
    score -= callback_penalty
    score = max(0, min(100, score))

    degraded = 1 if score < 100 else 0

    return DriveHealth(
        connected=1 if connected else 0,
        fault_active=1 if fault_active else 0,
        callback_errors_total=max(0, int(callback_errors_total)),
        startup_error_present=1 if startup_error_present else 0,
        telemetry_poll_s=poll_s,
        stale_threshold_s=stale_threshold_s,
        telemetry_age=telemetry_age,
        telemetry_stale=telemetry_stale,
        degraded=degraded,
        health_score=score,
    )


def decide_readiness(health: DriveHealth) -> ReadinessDecision:
    if health.connected == 0:
        return ReadinessDecision(status="not_ready", code="DRIVE_OFFLINE", http_status=503)
    if health.degraded == 1:
        return ReadinessDecision(status="degraded", code="DRIVE_DEGRADED", http_status=503)
    return ReadinessDecision(status="ready", code=None, http_status=200)
