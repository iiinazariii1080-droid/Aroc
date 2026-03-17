"""Test configuration for dynamic testing scenarios."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TestConfig:  # noqa: N801
    __test__ = False  # Tell pytest this is not a test class
    """Configuration parameters for dynamic tests."""

    # Timeouts (seconds)
    bringup_step_timeout_s: float = 3.0
    move_timeout_s: float = 20.0
    homing_timeout_s: float = 30.0
    fault_reset_timeout_s: float = 5.0

    # Tolerances
    position_tolerance: int = 5  # units
    glitch_eps: int = 2  # units (for monotonicity check)

    # Polling intervals (seconds)
    status_poll_interval_s: float = 0.05  # 50ms
    motion_poll_interval_s: float = 0.02  # 20ms for smooth position tracking

    # Debouncing
    debounce_consecutive: int = 3  # N consecutive confirmations

    # Protocol stability test
    protocol_stability_cycles: int = 100
    protocol_stability_interval_s: float = 0.05  # 50ms

    # Stop mid-move
    stop_mid_move_delay_s: float = 0.4  # 300-500ms, use 400ms

    # Convergence monitor
    convergence_window_s: float = 1.0
    convergence_min_reduction_pct: float = 10.0  # 10% reduction per window

    # Invariant checks
    invariant_transient_allowance_s: float = 0.2  # 200ms for transient violations

    # Retry policy for test operations
    test_retry_max_attempts: int = 2
    test_retry_base_delay_s: float = 0.1


def get_test_config() -> TestConfig:
    """Get test configuration, optionally overridden by environment variables."""
    return TestConfig(
        bringup_step_timeout_s=float(
            os.getenv("TEST_BRINGUP_TIMEOUT_S", "3.0")
        ),
        move_timeout_s=float(os.getenv("TEST_MOVE_TIMEOUT_S", "20.0")),
        homing_timeout_s=float(os.getenv("TEST_HOMING_TIMEOUT_S", "30.0")),
        fault_reset_timeout_s=float(
            os.getenv("TEST_FAULT_RESET_TIMEOUT_S", "5.0")
        ),
        position_tolerance=int(os.getenv("TEST_POSITION_TOLERANCE", "5")),
        glitch_eps=int(os.getenv("TEST_GLITCH_EPS", "2")),
        status_poll_interval_s=float(
            os.getenv("TEST_STATUS_POLL_INTERVAL_S", "0.05")
        ),
        motion_poll_interval_s=float(
            os.getenv("TEST_MOTION_POLL_INTERVAL_S", "0.02")
        ),
        debounce_consecutive=int(os.getenv("TEST_DEBOUNCE_CONSECUTIVE", "3")),
        protocol_stability_cycles=int(
            os.getenv("TEST_PROTOCOL_STABILITY_CYCLES", "100")
        ),
        protocol_stability_interval_s=float(
            os.getenv("TEST_PROTOCOL_STABILITY_INTERVAL_S", "0.05")
        ),
        stop_mid_move_delay_s=float(
            os.getenv("TEST_STOP_MID_MOVE_DELAY_S", "0.4")
        ),
        convergence_window_s=float(
            os.getenv("TEST_CONVERGENCE_WINDOW_S", "1.0")
        ),
        convergence_min_reduction_pct=float(
            os.getenv("TEST_CONVERGENCE_MIN_REDUCTION_PCT", "10.0")
        ),
        invariant_transient_allowance_s=float(
            os.getenv("TEST_INVARIANT_TRANSIENT_ALLOWANCE_S", "0.2")
        ),
        test_retry_max_attempts=int(
            os.getenv("TEST_RETRY_MAX_ATTEMPTS", "2")
        ),
        test_retry_base_delay_s=float(
            os.getenv("TEST_RETRY_BASE_DELAY_S", "0.1")
        ),
    )

