"""Extended health policy tests (TEST-02).

Covers edge cases beyond the baseline healthy/saturated scenarios:
- Single penalty -> intermediate score
- Score exactly at threshold -> ready
- Score one below threshold -> not ready
- Callback errors binary penalty
- Telemetry stale at exact boundary
"""

from app.domain.health import HealthWeights, compute_drive_health, decide_readiness


_WEIGHTS = HealthWeights(
    disconnected=50,
    startup_error=30,
    telemetry_stale=20,
    fault_active=30,
    callback_error_max=20,
)


def test_single_penalty_fault_active():
    """Only fault_active penalty -> score = 100 - 30 = 70."""
    h = compute_drive_health(
        connected=True,
        fault_active=True,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=_WEIGHTS,
        now_monotonic=100.5,
    )
    assert h.health_score == 70
    assert h.degraded is True
    assert h.ready is True  # 70 >= 50 threshold


def test_score_exactly_at_threshold_is_ready():
    """Score == threshold (50) -> ready."""
    # disconnected=50 -> score = 100 - 50 = 50
    h = compute_drive_health(
        connected=False,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=_WEIGHTS,
        now_monotonic=100.5,
        readiness_threshold=50,
    )
    assert h.health_score == 50
    assert h.ready is True


def test_score_one_below_threshold_not_ready():
    """Score < threshold -> not ready."""
    # disconnected(50) + telemetry_stale(20) = 70 -> score = 30
    h = compute_drive_health(
        connected=False,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=None,  # stale
        weights=_WEIGHTS,
        now_monotonic=100.0,
        readiness_threshold=50,
    )
    assert h.health_score == 30
    assert h.ready is False


def test_callback_errors_binary_penalty():
    """Any callback error > 0 applies full callback_error_max weight."""
    h1 = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=1,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=_WEIGHTS,
        now_monotonic=100.5,
    )
    h999 = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=999,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=_WEIGHTS,
        now_monotonic=100.5,
    )
    # Both get the same penalty (20)
    assert h1.health_score == h999.health_score == 80


def test_telemetry_stale_at_exact_boundary():
    """Telemetry age exactly at stale_threshold_s boundary.

    stale_threshold_s = max(2.0, poll_s * 3.0) = max(2.0, 1.5) = 2.0
    age = 2.0 -> NOT stale (> is strict, not >=)
    """
    h = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=98.0,
        weights=_WEIGHTS,
        now_monotonic=100.0,
    )
    # age = 2.0, threshold = 2.0; > is strict so NOT stale
    assert h.telemetry_stale is False
    assert h.health_score == 100


def test_telemetry_just_over_stale_boundary():
    """Telemetry age just over stale_threshold_s -> stale."""
    h = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=97.99,
        weights=_WEIGHTS,
        now_monotonic=100.0,
    )
    # age = 2.01, threshold = 2.0; stale
    assert h.telemetry_stale is True
    assert h.health_score == 80  # -20 for telemetry_stale


def test_decide_readiness_degraded_but_connected():
    """Connected but low score -> degraded (not offline)."""
    h = compute_drive_health(
        connected=True,
        fault_active=True,
        callback_errors_total=1,
        startup_error_present=True,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=None,
        weights=_WEIGHTS,
        now_monotonic=100.0,
        readiness_threshold=50,
    )
    decision = decide_readiness(h)
    assert decision.status == "degraded"
    assert decision.http_status == 503


def test_decide_readiness_ready():
    """Healthy system -> ready."""
    h = compute_drive_health(
        connected=True,
        fault_active=False,
        callback_errors_total=0,
        startup_error_present=False,
        telemetry_poll_s=0.5,
        last_telemetry_monotonic=100.0,
        weights=_WEIGHTS,
        now_monotonic=100.5,
    )
    decision = decide_readiness(h)
    assert decision.status == "ready"
    assert decision.http_status == 200
    assert decision.code is None
