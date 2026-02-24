"""Tests for teleop_server — session lifecycle and lifespan."""

import pytest
import aiohttp
from unittest.mock import AsyncMock, patch, MagicMock

from app.teleop_server import _get_session, teleop_app


@pytest.mark.asyncio
async def test_get_session_creates_new():
    """First call creates a session."""
    import app.teleop_server as mod
    old = mod._session
    mod._session = None
    try:
        sess = await _get_session()
        assert isinstance(sess, aiohttp.ClientSession)
        await sess.close()
    finally:
        mod._session = old


@pytest.mark.asyncio
async def test_get_session_reuses():
    """Subsequent calls return the same session."""
    import app.teleop_server as mod
    old = mod._session
    mod._session = None
    try:
        s1 = await _get_session()
        s2 = await _get_session()
        assert s1 is s2
        await s1.close()
    finally:
        mod._session = old


@pytest.mark.asyncio
async def test_get_session_recreates_if_closed():
    """If session is closed, a new one is created."""
    import app.teleop_server as mod
    old = mod._session
    mod._session = aiohttp.ClientSession()
    await mod._session.close()
    try:
        sess = await _get_session()
        assert not sess.closed
        await sess.close()
    finally:
        mod._session = old


@pytest.mark.asyncio
async def test_lifespan_cleanup():
    """Lifespan closes the session on exit."""
    import app.teleop_server as mod
    old = mod._session
    mod._session = aiohttp.ClientSession()
    try:
        from app.teleop_server import _lifespan
        async with _lifespan(teleop_app):
            pass
        assert mod._session is None
    finally:
        if mod._session and not mod._session.closed:
            await mod._session.close()
        mod._session = old
