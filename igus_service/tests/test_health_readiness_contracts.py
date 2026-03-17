from __future__ import annotations

import importlib
import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

import main
from app.system_routes import _compute_drive_health
from tests.fakes import FakeDrive, set_app_state


@pytest.fixture
def client(noop_lifecycle) -> Generator[TestClient, None, None]:
    set_app_state(main.app)
    with TestClient(main.app) as test_client:
        yield test_client


def test_ready_startup_transient_is_200_degraded_flag_set(client: TestClient) -> None:
    # Stale telemetry (score=80) is above the readiness threshold (50): service is ready
    # even though health.degraded=True.  This prevents Kubernetes pod restarts on transient
    # telemetry gaps that do not indicate a real service failure.
    set_app_state(main.app)
    main.app.state.drive_last_telemetry_monotonic = None

    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["code"] is None
    assert payload["health"]["degraded"] is True
    assert payload["health"]["telemetry_stale"] is True
    assert payload["health"]["score"] == 80


def test_ready_stale_is_200_degraded_flag_set(client: TestClient) -> None:
    # Same as above: stale-telemetry penalty alone keeps score at 80, above threshold 50.
    set_app_state(main.app)
    main.app.state.drive_last_telemetry_monotonic = time.monotonic() - 10.0

    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["code"] is None
    assert payload["health"]["degraded"] is True
    assert payload["health"]["telemetry_stale"] is True


def test_ready_offline_is_503_with_not_ready(client: TestClient) -> None:
    set_app_state(main.app)
    main.app.state.drive = FakeDrive(is_connected=False)

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["code"] == "DRIVE_OFFLINE"
    assert payload["driver_connected"] is False
    assert payload["health"]["degraded"] is True


def test_health_score_weights_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE", "5")
    monkeypatch.setenv("DRYVE_HEALTH_WEIGHT_DISCONNECTED", "70")

    from app.config import get_settings, reset_settings
    reset_settings()
    s = get_settings()

    class _State:
        pass

    state = _State()
    state.drive = FakeDrive(is_connected=True)
    state.drive_fault_active = False
    state.drive_telemetry_callback_errors_total = 0
    state.drive_last_error = None
    state.drive_last_telemetry_monotonic = None
    state.settings = s

    health = _compute_drive_health(state)

    assert health.telemetry_stale == 1
    assert health.health_score == 95