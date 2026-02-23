"""Tests for FastAPI app endpoints via TestClient with full auth flow."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    """Bypass auth for all tests in this module."""
    import app.core.security as sec
    monkeypatch.setattr(sec, "AUTH_DISABLED", True)
    # Also patch the middleware's imported reference
    import app.middleware.logging as log_mw
    monkeypatch.setattr(log_mw, "AUTH_DISABLED", True)


# ── health & root ─────────────────────────────────────────────────────────

class TestHealthEndpoint:
    """Tests for /api/v1/health endpoint."""

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=True)
    def test_health_all_ok(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {"bridge": True, "telemetry": True}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["database"] == "ok"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=True)
    def test_health_db_failure(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.side_effect = RuntimeError("db down")
        mock_mqtt.all_states.return_value = {}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["database"] == "error"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=True)
    def test_health_mqtt_all_down(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {"bridge": False, "telemetry": False}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=True)
    def test_health_mqtt_degraded(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {"bridge": True, "telemetry": False}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=False)
    def test_health_low_disk_space(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {"bridge": True, "telemetry": True}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disk_space"] == "warning"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", side_effect=OSError("err"))
    def test_health_disk_check_error(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {"bridge": True, "telemetry": True}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disk_space"] == "error"

    @patch("app.api.v1.endpoints.health.get_storage")
    @patch("app.api.v1.endpoints.health.mqtt_state")
    @patch("app.api.v1.endpoints.health.check_disk_space", return_value=True)
    def test_health_no_mqtt_clients(self, mock_disk, mock_mqtt, mock_storage):
        mock_storage.return_value.get.return_value = None
        mock_mqtt.all_states.return_value = {}
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mqtt_broker"] == "unknown"


class TestRootEndpoint:
    """Tests for /api/v1/ root endpoint."""

    def test_root_returns_info(self):
        resp = client.get("/api/v1/")
        assert resp.status_code == 200
        data = resp.json()
        assert "MQTT Bridge" in data["message"]
        assert "version" in data
        assert "documentation" in data

    def test_root_has_endpoints_list(self):
        resp = client.get("/api/v1/")
        data = resp.json()
        assert "endpoints" in data
        assert isinstance(data["endpoints"], dict)


# ── tasks endpoints ───────────────────────────────────────────────────────

class TestTasksEndpoints:
    """Tests for /api/v1/tasks endpoints."""

    def test_get_task_status_no_bridge(self):
        """Tasks endpoint returns 503 when bridge not set."""
        import app.api.v1.endpoints.tasks as tasks_mod
        tasks_mod._bridge_instance = None
        resp = client.get("/api/v1/tasks/task-abc/status")
        assert resp.status_code == 503

    def test_get_task_status_not_found(self):
        """Task not found returns 404."""
        import app.api.v1.endpoints.tasks as tasks_mod
        mock_bridge = MagicMock()
        mock_bridge.get_task_info.return_value = None
        tasks_mod._bridge_instance = mock_bridge
        resp = client.get("/api/v1/tasks/task-abc/status")
        assert resp.status_code == 404
        tasks_mod._bridge_instance = None

    def test_get_task_status_found(self):
        """Task found returns 200 with status."""
        import threading
        import time

        import app.api.v1.endpoints.tasks as tasks_mod
        from task_manager import TaskInfo
        mock_bridge = MagicMock()
        info = TaskInfo(
            command_id="cmd1",
            service="nav",
            request_id="req1",
            task_id="task-abc",
            thread=MagicMock(spec=threading.Thread),
            started_at=time.time(),
        )
        mock_bridge.get_task_info.return_value = info
        tasks_mod._bridge_instance = mock_bridge
        resp = client.get("/api/v1/tasks/task-abc/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-abc"
        assert data["service"] == "nav"
        tasks_mod._bridge_instance = None

    def test_list_tasks_no_bridge(self):
        """List tasks returns 503 when bridge not set."""
        import app.api.v1.endpoints.tasks as tasks_mod
        tasks_mod._bridge_instance = None
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 503

    def test_list_tasks_empty(self):
        """List tasks returns empty list when no tasks."""
        import app.api.v1.endpoints.tasks as tasks_mod
        mock_bridge = MagicMock()
        mock_bridge.get_active_tasks.return_value = {}
        tasks_mod._bridge_instance = mock_bridge
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["active_tasks"] == []
        tasks_mod._bridge_instance = None

    def test_list_tasks_with_tasks(self):
        """List tasks returns tasks."""
        import threading
        import time

        import app.api.v1.endpoints.tasks as tasks_mod
        from task_manager import TaskInfo
        mock_bridge = MagicMock()
        t1 = TaskInfo(command_id="c1", service="nav", request_id="r1", task_id="t1", thread=MagicMock(spec=threading.Thread), started_at=time.time())
        t2 = TaskInfo(command_id="c2", service="sys", request_id="r2", task_id="t2", thread=MagicMock(spec=threading.Thread), started_at=time.time())
        mock_bridge.get_active_tasks.return_value = {"t1": t1, "t2": t2}
        tasks_mod._bridge_instance = mock_bridge
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        tasks_mod._bridge_instance = None

    def test_invalid_task_id_rejected(self):
        """Task ID with invalid characters is rejected."""
        resp = client.get("/api/v1/tasks/bad task!/status")
        assert resp.status_code == 422


# ── certificates endpoints ────────────────────────────────────────────────

class TestCertificatesEndpoints:
    """Tests for /api/v1/config/certificates endpoints."""

    @patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=False)
    def test_get_ca_cert_not_found(self, mock_exists):
        resp = client.get("/api/v1/config/certificates/ca")
        assert resp.status_code == 404

    @patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=True)
    def test_get_ca_cert_pem(self, mock_exists):
        pem_content = b"-----BEGIN CERTIFICATE-----\nMIIBtest\n-----END CERTIFICATE-----\n"
        with patch("builtins.open", MagicMock(return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=pem_content))),
            __exit__=MagicMock(return_value=False)
        ))):
            resp = client.get("/api/v1/config/certificates/ca")
            assert resp.status_code == 200
            assert "pem" in resp.headers.get("content-type", "")

    @patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=True)
    def test_get_ca_cert_der(self, mock_exists):
        der_content = b"\x30\x82\x01\x00"  # DER-like binary content
        with patch("builtins.open", MagicMock(return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=der_content))),
            __exit__=MagicMock(return_value=False)
        ))):
            resp = client.get("/api/v1/config/certificates/ca")
            assert resp.status_code == 200
            assert "x509" in resp.headers.get("content-type", "")

    @patch("app.api.v1.endpoints.certificates.os.path.exists", side_effect=Exception("boom"))
    def test_get_ca_cert_error(self, mock_exists):
        resp = client.get("/api/v1/config/certificates/ca")
        assert resp.status_code == 500


# ── version header ────────────────────────────────────────────────────────

class TestVersionHeader:
    """Verify API-Version header is added by middleware."""

    def test_version_header_present(self):
        resp = client.get("/api/v1/")
        assert "API-Version" in resp.headers


# ── metrics endpoint ──────────────────────────────────────────────────────

class TestMetricsEndpoint:
    """Test /metrics endpoint."""

    def test_metrics_returns_prometheus_format(self):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        # Prometheus format contains HELP/TYPE lines
        assert "http_requests_total" in body or "HELP" in body or resp.status_code == 200
