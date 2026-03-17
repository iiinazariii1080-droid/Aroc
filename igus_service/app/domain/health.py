from __future__ import annotations

import time
from dataclasses import dataclass

from app import error_codes as _ec

# Health computation constants
MIN_POLL_INTERVAL_S = 0.05
MIN_STALE_THRESHOLD_S = 2.0
STALE_MULTIPLIER = 3.0


@dataclass(frozen=True)
class HealthWeights:
    disconnected: int
    startup_error: int
    telemetry_stale: int
    fault_active: int
    callback_error_max: int


@dataclass(frozen=True)
class DriveHealth:
    connected: bool
    fault_active: bool
    callback_errors_total: int
    startup_error_present: bool
    telemetry_poll_s: float
    stale_threshold_s: float
    telemetry_age: float
    telemetry_stale: bool
    degraded: bool
    ready: bool
    health_score: int


@dataclass(frozen=True)
class ReadinessDecision:
    status: str
    code: str | None
    http_status: int


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
    readiness_threshold: int = 50,
) -> DriveHealth:
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    poll_s = max(MIN_POLL_INTERVAL_S, float(telemetry_poll_s))
    stale_threshold_s = max(MIN_STALE_THRESHOLD_S, poll_s * STALE_MULTIPLIER)

    if last_telemetry_monotonic is None:
        telemetry_age = -1.0
    else:
        telemetry_age = max(0.0, now - float(last_telemetry_monotonic))

    telemetry_stale: bool = telemetry_age < 0.0 or telemetry_age > stale_threshold_s
    # Binary: any callback error applies the full configured weight; zero errors → no penalty.
    # Avoids conflating error count with penalty magnitude.
    callback_penalty = weights.callback_error_max if callback_errors_total > 0 else 0

    score = 100
    if not connected:
        score -= weights.disconnected
    if startup_error_present:
        score -= weights.startup_error
    if telemetry_stale:
        score -= weights.telemetry_stale
    if fault_active:
        score -= weights.fault_active
    score -= callback_penalty
    score = max(0, min(100, score))

    return DriveHealth(
        connected=connected,
        fault_active=fault_active,
        callback_errors_total=max(0, int(callback_errors_total)),
        startup_error_present=startup_error_present,
        telemetry_poll_s=poll_s,
        stale_threshold_s=stale_threshold_s,
        telemetry_age=telemetry_age,
        telemetry_stale=telemetry_stale,
        degraded=score < 100,
        ready=score >= int(readiness_threshold),
        health_score=score,
    )


def decide_readiness(health: DriveHealth) -> ReadinessDecision:
    if not health.connected:
        return ReadinessDecision(status="not_ready", code=_ec.DRIVE_OFFLINE.code, http_status=503)
    if not health.ready:
        return ReadinessDecision(status="degraded", code=_ec.DRIVE_DEGRADED.code, http_status=503)
    return ReadinessDecision(status="ready", code=None, http_status=200)
