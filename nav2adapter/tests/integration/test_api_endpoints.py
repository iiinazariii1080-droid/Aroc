"""
Integration tests for API endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from domain.models import NavigationStatus, PositionStatus, NavigationStatusEnum
from services.reliability_metrics import reliability_metrics
from main import app
from app.dependencies import get_symovo_client


@pytest.mark.asyncio
async def test_get_robots(test_client):
    """Test GET /api/v1/robots endpoint."""
    with patch("routes.aehub.settings") as mock_settings:
        mock_settings.robot_id = "fahrdummy-01"
        response = test_client.get("/api/v1/robots")
        assert response.status_code == 200
        data = response.json()
        assert "robots" in data
        assert len(data["robots"]) == 1
        assert data["robots"][0]["id"] == "fahrdummy-01"


@pytest.mark.asyncio
async def test_get_navigation_status(test_client):
    """Test GET /api/v1/robots/{robot_id}/status/navigation endpoint."""
    with patch("routes.aehub.settings") as mock_settings, \
         patch("routes.aehub.state_store") as mock_store:
        mock_settings.robot_id = "fahrdummy-01"
        mock_store.get_last_navigation_status = AsyncMock(
            return_value=NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
                eta_seconds=None,
                error_reason=None,
            )
        )
        
        response = test_client.get("/api/v1/robots/fahrdummy-01/status/navigation")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "idle"
        assert data["goal_id"] is None


@pytest.mark.asyncio
async def test_get_position_status(test_client):
    """Test GET /api/v1/robots/{robot_id}/status/position endpoint."""
    with patch("routes.aehub.settings") as mock_settings, \
         patch("routes.aehub.state_store") as mock_store:
        mock_settings.robot_id = "fahrdummy-01"
        mock_store.get_last_position_status = AsyncMock(
            return_value=PositionStatus(
                x=1.0,
                y=2.0,
                theta=0.5,
                frame_id="map",
            )
        )
        
        response = test_client.get("/api/v1/robots/fahrdummy-01/status/position")
        assert response.status_code == 200
        data = response.json()
        assert data["x"] == 1.0
        assert data["y"] == 2.0
        assert data["theta"] == 0.5
        assert data["frame_id"] == "map"


@pytest.mark.asyncio
async def test_get_position_status_default(test_client):
    """Test GET /api/v1/robots/{robot_id}/status/position returns default when no position."""
    with patch("routes.aehub.settings") as mock_settings, \
         patch("routes.aehub.state_store") as mock_store:
        mock_settings.robot_id = "fahrdummy-01"
        mock_store.get_last_position_status = AsyncMock(return_value=None)
        
        response = test_client.get("/api/v1/robots/fahrdummy-01/status/position")
        assert response.status_code == 200
        data = response.json()
        assert data["x"] == 0.0
        assert data["y"] == 0.0
        assert data["theta"] == 0.0
        assert data["frame_id"] == "map"


@pytest.mark.asyncio
async def test_get_robots_404(test_client):
    """Test GET /api/v1/robots/{robot_id} returns 404 for wrong robot."""
    with patch("routes.aehub.settings") as mock_settings:
        mock_settings.robot_id = "fahrdummy-01"
        response = test_client.get("/api/v1/robots/wrong_robot/status/navigation")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_healthz_endpoint(test_client):
    """Test GET /healthz endpoint."""
    response = test_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_healthz_contains_reliability_section(test_client):
    """Test GET /healthz returns reliability diagnostics payload."""
    response = test_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert "reliability" in data
    reliability = data["reliability"]
    assert isinstance(reliability.get("degraded"), bool)
    assert isinstance(reliability.get("reasons"), list)
    assert isinstance(reliability.get("snapshot"), dict)


def test_ops_reliabilityz_endpoint(test_client):
    """Test GET /ops/reliabilityz endpoint returns compact diagnostics."""
    response = test_client.get("/ops/reliabilityz?top_n=5")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "reliability" in data
    reliability = data["reliability"]
    assert isinstance(reliability.get("degraded"), bool)
    assert isinstance(reliability.get("reasons"), list)
    assert isinstance(reliability.get("thresholds"), dict)
    assert isinstance(reliability.get("uptime_s"), (int, float))
    assert isinstance(reliability.get("counter_total"), int)
    assert isinstance(reliability.get("top_counters"), list)
    assert isinstance(reliability.get("duration_total"), int)
    assert isinstance(reliability.get("top_duration_ms"), list)
    assert isinstance(reliability.get("rate_total"), int)
    assert isinstance(reliability.get("top_rates_per_sec"), list)


def test_ops_reliabilityz_latency_degraded_reason(test_client):
    """Latency threshold breach should produce degraded reason in reliability payload."""
    fake_snapshot = {
        "uptime_s": 123.0,
        "counters": {},
        "duration": {
            "mqtt.publish.event.latency_s": {
                "count": 20,
                "sum_s": 40.0,  # avg 2000ms
                "max_s": 3.0,
            }
        },
    }
    with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot):
        response = test_client.get("/ops/reliabilityz")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]
        assert reliability["degraded"] is True
        reasons = reliability.get("reasons", [])
        assert any("mqtt_event_latency" in reason for reason in reasons)


def test_ops_reliabilityz_counter_degraded_reason(test_client):
    """Counter threshold breach should produce degraded reason in reliability payload."""
    fake_snapshot = {
        "uptime_s": 99.0,
        "counters": {
            "mqtt.publish.event.failure": 3,
        },
        "duration": {},
    }
    with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot):
        response = test_client.get("/ops/reliabilityz")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]
        assert reliability["degraded"] is True
        reasons = reliability.get("reasons", [])
        assert any("mqtt_event_publish_failure" in reason for reason in reasons)


def test_ops_reliabilityz_latency_not_degraded_with_low_samples(test_client):
    """Latency reason should not appear when sample count is below HEALTH_LATENCY_MIN_SAMPLES."""
    fake_snapshot = {
        "uptime_s": 222.0,
        "counters": {},
        "duration": {
            "mqtt.publish.event.latency_s": {
                "count": 2,
                "sum_s": 10.0,
                "max_s": 6.0,
            }
        },
    }
    with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot), \
         patch("routes.health.HEALTH_LATENCY_MIN_SAMPLES", 10):
        response = test_client.get("/ops/reliabilityz")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]
        reasons = reliability.get("reasons", [])
        assert not any("mqtt_event_latency" in reason for reason in reasons)


def test_ops_reliabilityz_uses_runtime_metrics_updates(test_client):
    """Endpoint should reflect real runtime metric updates (no snapshot patching)."""
    reliability_metrics.reset()
    try:
        reliability_metrics.inc("mqtt.publish.event.success")
        reliability_metrics.observe_duration("mqtt.publish.event.latency_s", 0.2)
        reliability_metrics.observe_duration("mqtt.publish.event.latency_s", 0.1)

        response = test_client.get("/ops/reliabilityz?top_n=3")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]

        top_counters = reliability.get("top_counters", [])
        assert any(item.get("name") == "mqtt.publish.event.success" for item in top_counters)

        top_duration = reliability.get("top_duration_ms", [])
        mqtt_duration = next(
            (item for item in top_duration if item.get("name") == "mqtt.publish.event.latency_s"),
            None,
        )
        assert mqtt_duration is not None
        assert mqtt_duration.get("count") == 2
        assert float(mqtt_duration.get("avg_ms")) > 0
        assert float(mqtt_duration.get("max_ms")) >= float(mqtt_duration.get("avg_ms"))

        top_rates = reliability.get("top_rates_per_sec", [])
        mqtt_rate = next(
            (item for item in top_rates if item.get("name") == "mqtt.publish.event.success"),
            None,
        )
        assert mqtt_rate is not None
        assert float(mqtt_rate.get("rate_per_sec")) > 0
    finally:
        reliability_metrics.reset()


def test_ops_reliabilityz_rate_degraded_reason(test_client):
    """Rate threshold breach should produce degraded reason in reliability payload."""
    fake_snapshot = {
        "uptime_s": 333.0,
        "counters": {},
        "duration": {},
        "rates_60s": {
            "mqtt.publish.event.failure": 0.5,
        },
    }
    with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot):
        response = test_client.get("/ops/reliabilityz")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]
        assert reliability["degraded"] is True
        reasons = reliability.get("reasons", [])
        assert any("mqtt_event_failure_rate" in reason for reason in reasons)


def test_ops_reliabilityz_rate_not_degraded_below_threshold(test_client):
    """Rate reason should not appear when rolling rate is below configured threshold."""
    fake_snapshot = {
        "uptime_s": 444.0,
        "counters": {},
        "duration": {},
        "rates_60s": {
            "mqtt.publish.event.failure": 0.001,
        },
    }
    with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot), \
         patch("routes.health.HEALTH_MQTT_EVENT_FAILURE_RATE_MAX_PER_S", 0.01):
        response = test_client.get("/ops/reliabilityz")
        assert response.status_code == 200
        data = response.json()
        reliability = data["reliability"]
        reasons = reliability.get("reasons", [])
        assert not any("mqtt_event_failure_rate" in reason for reason in reasons)


def test_ops_reliability_prom_endpoint(test_client):
    """Prometheus endpoint should return plain text metrics for reliability diagnostics."""
    reliability_metrics.reset()
    try:
        reliability_metrics.inc("mqtt.publish.event.success", 2)
        reliability_metrics.observe_duration("mqtt.publish.event.latency_s", 0.12)

        response = test_client.get("/ops/reliability.prom")
        assert response.status_code == 200
        body = response.text
        assert "nav2adapter_reliability_degraded" in body
        assert "nav2adapter_reliability_counter" in body
        assert 'name="mqtt.publish.event.success"' in body
        assert "nav2adapter_reliability_rate_per_sec" in body
        assert "nav2adapter_reliability_duration_avg_ms" in body
    finally:
        reliability_metrics.reset()


def _install_symovo_override(mock_client) -> None:
    async def _override():
        yield mock_client

    app.dependency_overrides[get_symovo_client] = _override


def _clear_symovo_override() -> None:
    app.dependency_overrides.pop(get_symovo_client, None)


def test_symovo_v1_map_list(test_client):
    mock_client = AsyncMock()
    mock_client.map = AsyncMock(return_value={"result": [{"id": 1, "name": "factory"}]})
    _install_symovo_override(mock_client)
    try:
        response = test_client.get("/api/v1/symovo/map")
        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        assert data["result"][0]["id"] == 1
    finally:
        _clear_symovo_override()


def test_symovo_v1_map_full_png(test_client):
    mock_client = AsyncMock()
    mock_client.map_png = AsyncMock(return_value=b"PNGDATA")
    _install_symovo_override(mock_client)
    try:
        response = test_client.get("/api/v1/symovo/map/7/full.png")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == b"PNGDATA"
    finally:
        _clear_symovo_override()


def test_symovo_v1_map_tile_png(test_client):
    mock_client = AsyncMock()
    mock_client.map_tile_png = AsyncMock(return_value=b"TILE")
    _install_symovo_override(mock_client)
    try:
        response = test_client.get("/api/v1/symovo/map/7/2/10/11.png")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == b"TILE"
    finally:
        _clear_symovo_override()


def test_symovo_v1_slam_state(test_client):
    mock_client = AsyncMock()
    mock_client.slam_state = AsyncMock(return_value={"state": "RUNNING"})
    _install_symovo_override(mock_client)
    try:
        response = test_client.get("/api/v1/symovo/slam/state")
        assert response.status_code == 200
        assert response.json()["state"] == "RUNNING"
    finally:
        _clear_symovo_override()


def test_symovo_v1_lidar_scan_png(test_client):
    mock_client = AsyncMock()
    mock_client.scan_png = AsyncMock(return_value=b"SCAN")
    _install_symovo_override(mock_client)
    try:
        response = test_client.get("/api/v1/symovo/lidar/scan.png")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == b"SCAN"
    finally:
        _clear_symovo_override()


def test_symovo_v1_lidar_raw_capability(test_client):
    response = test_client.get("/api/v1/symovo/lidar/raw")
    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is False
    assert payload["formats"] == []
