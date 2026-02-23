"""Test utilities for dynamic testing of dryve D1 driver.

This package provides:
- Assertions: Eventually, Always, debounced predicates
- Monitors: Monotonicity, Convergence, CiA402 invariants, Reconnection
- Metrics: TestMetrics for latency, disconnect tracking, position samples
- Logging: Structured test logging with stages, samples, requests
- Config: Test configuration (timeouts, tolerances, polling intervals)
"""

from .assertions import Eventually, Always, debounced_predicate
from .monitors import (
    MonotonicityMonitor,
    ConvergenceMonitor,
    CiA402InvariantMonitor,
    ReconnectionMonitor,
)
from .metrics import TestMetrics
from .logging import TestLogger
from .config import TestConfig, get_test_config
from .test_api import TestDriveController, TestAPIError, get_test_api_url

__all__ = [
    "Eventually",
    "Always",
    "debounced_predicate",
    "MonotonicityMonitor",
    "ConvergenceMonitor",
    "CiA402InvariantMonitor",
    "ReconnectionMonitor",
    "TestMetrics",
    "TestLogger",
    "TestConfig",
    "get_test_config",
    "TestDriveController",
    "TestAPIError",
    "get_test_api_url",
]

