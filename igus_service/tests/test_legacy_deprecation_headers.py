from __future__ import annotations

import dataclasses

from fastapi.testclient import TestClient

import main
from app import config
from app.config import get_settings
from tests.fakes import set_app_state


def test_legacy_status_has_deprecation_headers(noop_lifecycle) -> None:
    set_app_state(main.app)

    with TestClient(main.app) as client:
        response = client.get("/status")
        metrics_response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    assert response.headers.get("Sunset") is not None
    assert "rel=\"alternate\"" in response.headers.get("Link", "")
    assert "igus_legacy_api_requests_total{path=\"/status\",phase=\"deprecated\"}" in metrics_response.text


def test_v1_status_has_no_deprecation_headers(noop_lifecycle) -> None:
    set_app_state(main.app)

    with TestClient(main.app) as client:
        response = client.get("/drive/status")

    assert response.status_code == 200
    assert response.headers.get("Deprecation") is None


def test_legacy_removed_phase_returns_410(noop_lifecycle) -> None:
    set_app_state(main.app)

    prev_settings = config._settings
    config._settings = dataclasses.replace(get_settings(), legacy_api_phase="removed")
    try:
        with TestClient(main.app) as client:
            response = client.get("/status")
            v1_response = client.get("/drive/status")
            metrics_response = client.get("/metrics")
    finally:
        config._settings = prev_settings

    assert response.status_code == 410
    assert response.json()["code"] == "LEGACY_API_REMOVED"
    assert response.headers.get("X-API-Phase") == "removed"
    assert v1_response.status_code == 200
    assert 'igus_legacy_api_phase{phase="removed"} 1' in metrics_response.text
    assert 'igus_legacy_api_requests_total{path="/status",phase="removed"}' in metrics_response.text


def test_invalid_legacy_phase_falls_back_to_deprecated(noop_lifecycle) -> None:
    set_app_state(main.app)

    prev_settings = config._settings
    config._settings = dataclasses.replace(get_settings(), legacy_api_phase="invalid-phase")
    try:
        with TestClient(main.app) as client:
            response = client.get("/status")
            metrics_response = client.get("/metrics")
    finally:
        config._settings = prev_settings

    assert response.status_code == 200
    assert response.headers.get("X-API-Phase") == "deprecated"
    assert 'igus_legacy_api_requests_total{path="/status",phase="deprecated"}' in metrics_response.text
    assert 'igus_legacy_api_phase{phase="deprecated"} 1' in metrics_response.text
