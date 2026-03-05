"""Tests for teleop_server — session lifecycle and lifespan."""

import pytest
import aiohttp
from unittest.mock import AsyncMock, patch, MagicMock

from app.teleop_server import teleop_app, _lifespan


@pytest.mark.asyncio
async def test_lifespan_creates_session():
    """Lifespan creates app.state.session on startup."""
    async with _lifespan(teleop_app):
        assert hasattr(teleop_app.state, "session")
        assert isinstance(teleop_app.state.session, aiohttp.ClientSession)
        assert not teleop_app.state.session.closed
    # After lifespan exit, session should be cleaned up
    assert teleop_app.state.session is None


@pytest.mark.asyncio
async def test_lifespan_closes_session():
    """Lifespan closes the session on exit."""
    session_ref = None
    async with _lifespan(teleop_app):
        session_ref = teleop_app.state.session
        assert not session_ref.closed
    # After exit, the original session is closed
    assert session_ref.closed


@pytest.mark.asyncio
async def test_lifespan_session_is_bound_to_current_loop():
    """Session created in lifespan should work in the current event loop."""
    async with _lifespan(teleop_app):
        session = teleop_app.state.session
        assert isinstance(session, aiohttp.ClientSession)
        # Session should be usable (not raise RuntimeError for wrong loop)
        assert not session.closed
