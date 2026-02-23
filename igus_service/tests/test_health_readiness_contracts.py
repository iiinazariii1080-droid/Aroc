from __future__ import annotations

import importlib
import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

import main
from app.system_routes import _compute_drive_health
from tests.conftest import FakeDrive, set_app_state


@pytest.fixture
def client(noop_lifecycle) -> Generator[TestClient, None, None]:
    set_app_state(main.app)
    with TestClient(main.app) as test_client:
        yield test_client


def test_ready_startup_transient_is_503_with_degraded(client: TestClient) -> None:
    set_app_state(main.app)
    main.app.state.drive_last_telemetry_monotonic = None

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["code"] == "DRIVE_DEGRADED"
    assert payload["health"]["degraded"] is True
    assert payload["health"]["telemetry_stale"] is True
    assert payload["health"]["score"] == 80


def test_ready_stale_is_503_with_degraded(client: TestClient) -> None:
    set_app_state(main.app)
    main.app.state.drive_last_telemetry_monotonic = time.monotonic() - 10.0

    response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["code"] == "DRIVE_DEGRADED"
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

    import app.config as app_config
    importlib.reload(app_config)

    class _State:
        pass

    state = _State()
    state.drive = FakeDrive(is_connected=True)
    state.drive_fault_active = False
    state.drive_telemetry_callback_errors_total = 0
    state.drive_last_error = None
    state.drive_last_telemetry_monotonic = None
    state.settings = {
        "DRYVE_TELEMETRY_POLL_S": 0.5,
        "DRYVE_HEALTH_WEIGHT_DISCONNECTED": app_config.DRYVE_HEALTH_WEIGHT_DISCONNECTED,
        "DRYVE_HEALTH_WEIGHT_STARTUP_ERROR": app_config.DRYVE_HEALTH_WEIGHT_STARTUP_ERROR,
        "DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE": app_config.DRYVE_HEALTH_WEIGHT_TELEMETRY_STALE,
        "DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE": app_config.DRYVE_HEALTH_WEIGHT_FAULT_ACTIVE,
        "DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX": app_config.DRYVE_HEALTH_WEIGHT_CALLBACK_ERROR_MAX,
    }

    health = _compute_drive_health(state)

    assert health.telemetry_stale == 1
    assert health.health_score == 95