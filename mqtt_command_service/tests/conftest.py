"""
Pytest configuration and shared fixtures.
"""
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set test environment variables
os.environ.setdefault("CONFIG_DB_PATH", ":memory:")
os.environ.setdefault("API_ENABLED", "false")

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture()
def bypass_auth(monkeypatch):
    """Bypass auth for tests that need it (non-autouse — opt-in)."""
    import app.core.security as sec
    import app.middleware.logging as log_mw
    monkeypatch.setattr(sec, "AUTH_DISABLED", True)
    monkeypatch.setattr(log_mw, "AUTH_DISABLED", True)


@pytest.fixture()
def client(bypass_auth):
    """TestClient with auth bypassed. Import `app` lazily to avoid side effects."""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mock_config_service():
    """Mock ConfigService returned by get_config_service()."""
    svc = MagicMock()
    cfg = MagicMock()
    cfg.broker = "192.168.1.100"
    cfg.broker_port = 1883
    cfg.mqtt_user = "admin"
    cfg.mqtt_password = "secret"
    cfg.mqtt_use_tls = False
    cfg.mqtt_tls_insecure = False
    svc.get_config.return_value = cfg
    svc.update_config.return_value = True
    return svc


@pytest.fixture()
def mock_storage():
    """Mock config_storage.get_storage()."""
    storage = MagicMock()
    storage.get.return_value = None
    storage.get_all.return_value = {}
    storage.get_json.return_value = None
    return storage

