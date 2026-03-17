"""Shared fixtures for janus_camera_page tests."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)


@pytest.fixture
def app():
    """Create a test-safe app instance with mocked system services."""
    # Patch event handlers that might hit hardware (watchdogs, v4l2)
    with patch("app.core.events.register_event_handlers", lambda app: None):
        from app.core.app import create_app
        return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
