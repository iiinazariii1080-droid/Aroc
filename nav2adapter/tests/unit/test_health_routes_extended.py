"""Tests for routes/health.py — env helpers, livez, readyz, prometheus metrics, reliability thresholds."""
import time
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


# ── _env_float / _env_int helpers ────────────────────────────────────

class TestEnvHelpers:
    def test_env_float_invalid(self):
        with patch.dict("os.environ", {"TEST_F": "not_a_float"}):
            from routes.health import _env_float
            result = _env_float("TEST_F", 3.14)
        assert result == 3.14

    def test_env_int_invalid(self):
        with patch.dict("os.environ", {"TEST_I": "not_an_int"}):
            from routes.health import _env_int
            result = _env_int("TEST_I", 42)
        assert result == 42


# ── /livez ───────────────────────────────────────────────────────────

class TestLivez:
    def test_livez_ok(self, test_client):
        resp = test_client.get("/livez")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_livez_stale_heartbeat(self, test_client):
        from main import app
        # Set heartbeat far in the past
        from unittest.mock import PropertyMock
        old = getattr(app.state, "last_heartbeat_ts", time.time())
        app.state.last_heartbeat_ts = 0.0  # epoch → very stale
        try:
            resp = test_client.get("/livez")
        finally:
            app.state.last_heartbeat_ts = old
        # Should be 503 (stale heartbeat)
        assert resp.status_code == 503


# ── /readyz ──────────────────────────────────────────────────────────

class TestReadyz:
    def test_readyz_ok(self, test_client):
        resp = test_client.get("/readyz")
        assert resp.status_code == 200

    def test_readyz_not_started(self, test_client):
        from main import app
        old = getattr(app.state, "startup_ok", True)
        app.state.startup_ok = False
        try:
            resp = test_client.get("/readyz")
        finally:
            app.state.startup_ok = old
        assert resp.status_code == 503

    def test_readyz_mqtt_disconnected_degraded(self, test_client):
        """MQTT disconnect should NOT fail readyz — it's degraded, not broken."""
        from main import app
        mqtt = MagicMock()
        mqtt.is_connected = False
        mqtt._connected = False
        old_ma = getattr(app.state, "mqtt_adapter", None)
        app.state.mqtt_adapter = mqtt
        try:
            resp = test_client.get("/readyz")
        finally:
            app.state.mqtt_adapter = old_ma
        # Should still be 200 (degraded but functional)
        assert resp.status_code == 200


# ── /ops/reliabilityz ────────────────────────────────────────────────

class TestReliabilityz:
    def test_reliabilityz_basic(self, test_client):
        resp = test_client.get("/ops/reliabilityz")
        assert resp.status_code == 200
        data = resp.json()
        assert "reliability" in data

    def test_reliabilityz_rate_degraded(self, test_client):
        fake_snapshot = {
            "uptime_s": 100.0,
            "counters": {},
            "duration": {},
            "rates_60s": {
                "eventbus.publish.drop_oldest": 0.5,  # very high
            },
        }
        with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot):
            resp = test_client.get("/ops/reliabilityz")
        data = resp.json()
        reliability = data["reliability"]
        assert reliability["degraded"] is True
        assert any("eventbus_drop_rate" in r for r in reliability["reasons"])


# ── /ops/reliability.prom (prometheus) ────────────────────────────────

class TestPrometheus:
    def test_metricsz_returns_text(self, test_client):
        resp = test_client.get("/ops/reliability.prom")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        text = resp.text
        assert "nav2adapter_reliability_degraded" in text

    def test_metricsz_with_duration_data(self, test_client):
        fake_snapshot = {
            "uptime_s": 60.0,
            "counters": {"test.counter": 42},
            "duration": {"test.latency": {"count": 5, "sum_s": 1.0, "max_s": 0.5}},
            "rates_60s": {"test.rate": 0.1},
        }
        with patch("routes.health.reliability_metrics.snapshot", return_value=fake_snapshot):
            resp = test_client.get("/ops/reliability.prom")
        text = resp.text
        assert "test.counter" in text
        assert "test.latency" in text
        assert "test.rate" in text
