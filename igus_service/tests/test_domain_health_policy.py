from app.domain.health import (
    HealthWeights,
    compute_drive_health,
    decide_readiness,
    resolve_weights,
)


def test_compute_drive_health_healthy_baseline() -> None:
    weights = HealthWeights(
        disconnected=50,
        startup_error=30,
        telemetry_stale=20,
        fault_active=30,
        callback_error_max=20,
    )

    health = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=weights,
        now_monotonic=101.0,
    )

    assert health.connected == 1
    assert health.telemetry_stale == 0
    assert health.degraded == 0
    assert health.health_score == 100


def test_compute_drive_health_penalties_saturate_to_zero() -> None:
    weights = HealthWeights(
        disconnected=80,
        startup_error=50,
        telemetry_stale=40,
        fault_active=40,
        callback_error_max=30,
    )

    health = compute_drive_health(
        connected=False,
        fault_active=True,
        callback_errors_total=999,
        startup_error_present=True,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=None,
        weights=weights,
        now_monotonic=100.0,
    )

    assert health.telemetry_stale == 1
    assert health.callback_errors_total == 999
    assert health.health_score == 0
    assert health.degraded == 1


def test_resolve_weights_clamps_negative_values() -> None:
    defaults = HealthWeights(
        disconnected=50,
        startup_error=30,
        telemetry_stale=20,
        fault_active=30,
        callback_error_max=20,
    )

    resolved = resolve_weights(
        {
            "DRYVE_HEALTH_WEIGHT_DISCONNECTED": "-1",
            "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR": "-2",
            "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE": "-3",
            "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE": "-4",
            "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX": "-5",
        },
        defaults,
    )

    assert resolved.disconnected == 0
    assert resolved.startup_error == 0
    assert resolved.telemetry_stale == 0
    assert resolved.fault_active == 0
    assert resolved.callback_error_max == 0


def test_decide_readiness_priority_offline_over_degraded() -> None:
    weights = HealthWeights(
        disconnected=50,
        startup_error=30,
        telemetry_stale=20,
        fault_active=30,
        callback_error_max=20,
    )

    health = compute_drive_health(
        connected=False,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=weights,
        now_monotonic=110.0,
    )

    decision = decide_readiness(health)

    assert decision.status == "not_ready"
    assert decision.code == "DRIVE_OFFLINE"
    assert decision.http_status == 503
