"""
Unit tests for CircuitBreaker.
"""
from unittest.mock import patch

import pytest

from exceptions import DeviceConnectionError
from services.circuit_breaker import CircuitBreaker


@pytest.fixture
def breaker() -> CircuitBreaker:
    """CircuitBreaker with low threshold for fast tests."""
    return CircuitBreaker(name="test", failure_threshold=3, reset_timeout_s=10.0)


# -- Initial state -------------------------------------------------------------


def test_starts_closed(breaker: CircuitBreaker):
    assert breaker.state == "closed"


def test_allow_request_when_closed(breaker: CircuitBreaker):
    assert breaker.allow_request() is True


# -- Failure counting ----------------------------------------------------------


def test_failures_below_threshold_stays_closed(breaker: CircuitBreaker):
    """threshold-1 failures keep the circuit CLOSED."""
    for _ in range(breaker.failure_threshold - 1):
        breaker.record_failure()

    assert breaker.state == "closed"
    assert breaker.allow_request() is True


def test_failures_at_threshold_trips_open(breaker: CircuitBreaker):
    """Reaching the failure threshold trips the circuit to OPEN."""
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    assert breaker.state == "open"


# -- OPEN state ----------------------------------------------------------------


def test_open_denies_requests(breaker: CircuitBreaker):
    """OPEN state: allow_request returns False."""
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    assert breaker.allow_request() is False


def test_open_guard_raises(breaker: CircuitBreaker):
    """OPEN state: guard() raises DeviceConnectionError."""
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    with pytest.raises(DeviceConnectionError, match="circuit_open"):
        breaker.guard()


# -- OPEN -> HALF_OPEN transition ----------------------------------------------


def test_open_transitions_to_half_open_after_timeout(breaker: CircuitBreaker):
    """After reset_timeout_s the circuit moves from OPEN to HALF_OPEN."""
    base = 1000.0

    with patch("services.circuit_breaker.time.monotonic", return_value=base):
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()

    # Just before timeout — still OPEN
    with patch("services.circuit_breaker.time.monotonic", return_value=base + 9.9):
        assert breaker.state == "open"

    # At timeout — transitions to HALF_OPEN
    with patch("services.circuit_breaker.time.monotonic", return_value=base + 10.0):
        assert breaker.state == "half_open"


# -- HALF_OPEN state -----------------------------------------------------------


def test_half_open_allows_one_request(breaker: CircuitBreaker):
    """HALF_OPEN allows at least one probe request."""
    base = 1000.0

    with patch("services.circuit_breaker.time.monotonic", return_value=base):
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()

    with patch("services.circuit_breaker.time.monotonic", return_value=base + 10.0):
        assert breaker.allow_request() is True


def test_half_open_success_closes_circuit(breaker: CircuitBreaker):
    """HALF_OPEN + record_success -> CLOSED."""
    base = 1000.0

    with patch("services.circuit_breaker.time.monotonic", return_value=base):
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()

    with patch("services.circuit_breaker.time.monotonic", return_value=base + 10.0):
        # Trigger transition to HALF_OPEN
        assert breaker.state == "half_open"
        breaker.record_success()

    assert breaker.state == "closed"
    assert breaker.allow_request() is True


def test_half_open_failure_reopens_circuit(breaker: CircuitBreaker):
    """HALF_OPEN + record_failure -> OPEN (immediately)."""
    base = 1000.0

    with patch("services.circuit_breaker.time.monotonic", return_value=base):
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()

    with patch("services.circuit_breaker.time.monotonic", return_value=base + 10.0):
        # Trigger transition to HALF_OPEN
        assert breaker.state == "half_open"
        breaker.record_failure()

    # Should be OPEN again (need fresh monotonic for the check)
    with patch("services.circuit_breaker.time.monotonic", return_value=base + 10.0):
        assert breaker.state == "open"


# -- record_success resets count -----------------------------------------------


def test_record_success_resets_failure_count(breaker: CircuitBreaker):
    """record_success resets the failure counter back to 0."""
    breaker.record_failure()
    breaker.record_failure()
    assert breaker._failure_count == 2

    breaker.record_success()
    assert breaker._failure_count == 0
    assert breaker.state == "closed"


def test_record_success_noop_when_clean(breaker: CircuitBreaker):
    """record_success on a fresh breaker is a no-op (no state change)."""
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker._failure_count == 0
