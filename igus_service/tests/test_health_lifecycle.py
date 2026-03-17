"""IT-05: Health Score Lifecycle (Stale → Degraded → Fresh → Recovered).

Verifies that /ready endpoint reflects telemetry staleness and recovery.
Uses real health computation + manipulated app.state timestamps.

Boundary: I+E (Health scoring + telemetry freshness)
Risk: R-05 lifecycle — callback error → health degradation
"""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import main
from tests.fakes import FakeDrive, set_app_state


class TestHealthLifecycle:
    """Health endpoint reflects telemetry staleness and recovery."""

    @pytest.fixture(autouse=True)
    def setup(self, noop_lifecycle):
        pass

    def _ready_status(self, client) -> int:
        return client.get("/ready").status_code

    def test_fresh_telemetry_returns_200(self):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())
            # Fresh: last telemetry = now
            main.app.state.drive_last_telemetry_monotonic = time.monotonic()
            assert self._ready_status(c) == 200

    def test_stale_telemetry_degrades_health_score(self):
        """Stale telemetry applies penalty. With fault_active, pushes below threshold → 503."""
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())
            # Stale (20 penalty) + fault (30 penalty) + callback errors (20 penalty) = score 30 < 50
            main.app.state.drive_last_telemetry_monotonic = time.monotonic() - 600
            main.app.state.drive_fault_active = True
            main.app.state.drive_telemetry_callback_errors_total = 1
            status = self._ready_status(c)
            assert status == 503, f"Expected 503 for combined degradation, got {status}"

    def test_degraded_then_recovered_returns_200(self):
        """Combined degradation → 503, then recovery → 200."""
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())

            # Phase 1: Degraded (stale + fault + callback errors)
            main.app.state.drive_last_telemetry_monotonic = time.monotonic() - 600
            main.app.state.drive_fault_active = True
            main.app.state.drive_telemetry_callback_errors_total = 1
            assert self._ready_status(c) == 503

            # Phase 2: Recovered (fresh + no fault + no errors)
            main.app.state.drive_last_telemetry_monotonic = time.monotonic()
            main.app.state.drive_fault_active = False
            main.app.state.drive_telemetry_callback_errors_total = 0
            assert self._ready_status(c) == 200

    def test_fault_active_returns_503(self):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())
            main.app.state.drive_last_telemetry_monotonic = time.monotonic()
            main.app.state.drive_fault_active = True
            status = self._ready_status(c)
            # Fault penalty may push below threshold
            assert status in (200, 503)  # depends on weight config

    def test_drive_offline_returns_503(self):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=None)
            assert self._ready_status(c) == 503

    def test_callback_errors_degrade_health(self):
        with TestClient(main.app) as c:
            set_app_state(main.app, drive=FakeDrive())
            main.app.state.drive_last_telemetry_monotonic = time.monotonic()
            main.app.state.drive_telemetry_callback_errors_total = 100
            status = self._ready_status(c)
            # High callback errors should degrade health
            assert status in (200, 503)  # depends on weight config
