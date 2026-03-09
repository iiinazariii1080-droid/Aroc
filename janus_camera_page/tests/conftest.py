"""Shared fixtures for janus_camera_page tests."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MONOREPO_ROOT = os.path.abspath(os.path.join(_SERVICE_ROOT, ".."))
for _p in (_SERVICE_ROOT, _MONOREPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def settings():
    """Return test settings instance."""
    from app.core.settings import Settings
    return Settings()


@pytest.fixture
def app():
    """Create a test-safe app instance with mocked event handlers."""
    with patch("app.core.events.register_event_handlers", lambda app: None), \
         patch.dict(os.environ, {"CAM_ADMIN_ENFORCE": "0"}):
        # Reload admin module so it picks up the test override
        import app.core.admin as _admin
        _admin._ENFORCE = False
        from app.core.app import create_app
        return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
