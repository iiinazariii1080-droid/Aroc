"""Shared fixtures for frontend_service tests."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)


@pytest.fixture
def app():
    """Return the FastAPI app instance."""
    import main as app_mod
    return app_mod.app


@pytest.fixture
async def client(app):
    """Async httpx test client for the frontend app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
