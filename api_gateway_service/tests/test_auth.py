"""Tests for AuthClient — token refresh lifecycle, error handling, describe()."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth_client import AuthClient
from app.core.auth_service import AuthContext, AuthSetupError


def _mock_app_with_client():
    app = MagicMock()
    app.state.http_client = MagicMock()
    return app


def _make_context():
    return AuthContext(
        base_url="http://hub.test",
        auth_url="http://hub.test/auth/robot",
        robot_id="bot-1",
        api_key="key-123",
        notes=None,
    )


@pytest.mark.asyncio
async def test_startup_and_shutdown():
    """AuthClient starts a refresh task on startup and stops it on shutdown."""
    client = AuthClient(min_retry_interval_s=0.05)
    app = _mock_app_with_client()

    with patch("app.core.auth_client.load_auth_context", new_callable=AsyncMock, side_effect=AuthSetupError("no config")):
        await client.startup(app)
        assert client._refresh_task is not None
        await client.shutdown()
        assert client._stop_event.is_set()


@pytest.mark.asyncio
async def test_refresh_stores_token():
    """Successful refresh stores the access token and expiry."""
    client = AuthClient()
    app = _mock_app_with_client()

    mock_data = {"access_token": "tok-abc", "expires_in": 3600}

    with (
        patch("app.core.auth_client.load_auth_context", new_callable=AsyncMock, return_value=_make_context()),
        patch("app.core.auth_client.request_robot_token", new_callable=AsyncMock, return_value=mock_data),
    ):
        await client.startup(app)

    assert client._token == "tok-abc"
    assert client._expires_at > time.time()
    assert client._last_error is None

    await client.shutdown()


@pytest.mark.asyncio
async def test_refresh_failure_sets_error():
    """Failed refresh records the error but doesn't crash."""
    client = AuthClient()
    app = _mock_app_with_client()

    with patch("app.core.auth_client.load_auth_context", new_callable=AsyncMock, side_effect=AuthSetupError("missing key")):
        await client.startup(app)

    assert client._token is None
    assert client._last_error == "missing key"

    await client.shutdown()


@pytest.mark.asyncio
async def test_get_access_token_raises_on_failure():
    """get_access_token raises RuntimeError when token cannot be obtained."""
    client = AuthClient()
    app = _mock_app_with_client()

    with patch("app.core.auth_client.load_auth_context", new_callable=AsyncMock, side_effect=AuthSetupError("no key")):
        await client.startup(app)
        with pytest.raises((RuntimeError, AuthSetupError)):
            await client.get_access_token()

    await client.shutdown()


@pytest.mark.asyncio
async def test_get_auth_header():
    """get_auth_header returns Bearer prefix."""
    client = AuthClient()
    app = _mock_app_with_client()
    mock_data = {"access_token": "my-jwt", "expires_in": 7200}

    with (
        patch("app.core.auth_client.load_auth_context", new_callable=AsyncMock, return_value=_make_context()),
        patch("app.core.auth_client.request_robot_token", new_callable=AsyncMock, return_value=mock_data),
    ):
        await client.startup(app)
        header = await client.get_auth_header()

    assert header == "Bearer my-jwt"
    await client.shutdown()


@pytest.mark.asyncio
async def test_describe_fields():
    """describe() returns expected structure."""
    client = AuthClient()
    info = client.describe()
    assert info["token_present"] is False
    assert info["last_error"] is None
    assert "context" in info
    assert info["context"]["base_url"] is None


@pytest.mark.asyncio
async def test_expires_soon_locked():
    """Token is considered expired soon when within refresh skew window."""
    client = AuthClient(refresh_skew_s=60.0)
    client._token = "tok"
    client._expires_at = time.time() + 30  # 30s left, skew is 60s → expired soon
    assert client._expires_soon_locked() is True

    client._expires_at = time.time() + 120  # 120s left, skew is 60s → not yet
    assert client._expires_soon_locked() is False
