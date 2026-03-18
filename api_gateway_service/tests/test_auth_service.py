"""Unit tests for app.core.auth_service — load_auth_context + request_robot_token."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.auth_service import (
    AuthContext,
    AuthSetupError,
    load_auth_context,
    request_robot_token,
)


# ── load_auth_context ─────────────────────────────────────────


class TestLoadAuthContextErrors:
    async def test_raises_when_no_hub_config(self):
        with patch("app.core.auth_service.hub_state_store") as store:
            store.get_hub_config = AsyncMock(return_value=None)
            with pytest.raises(AuthSetupError, match="Hub configuration is not set"):
                await load_auth_context()

    async def test_raises_when_hub_config_missing_base_url(self):
        with patch("app.core.auth_service.hub_state_store") as store:
            store.get_hub_config = AsyncMock(return_value={"notes": "no base_url"})
            with pytest.raises(AuthSetupError, match="Hub configuration is not set"):
                await load_auth_context()

    async def test_raises_when_no_robot_id(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={"base_url": "http://hub"})
            store.get_robot_credentials_meta = AsyncMock(return_value=None)
            with pytest.raises(AuthSetupError, match="Robot ID is not configured"):
                await load_auth_context()

    async def test_raises_when_no_api_key(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={"base_url": "http://hub"})
            store.get_robot_credentials_meta = AsyncMock(return_value={"robot_id": "bot-1"})
            secret.get_api_key = AsyncMock(return_value=None)
            with pytest.raises(AuthSetupError, match="Robot API key is not configured"):
                await load_auth_context()

    async def test_raises_on_relative_auth_url(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={
                "base_url": "http://hub",
                "auth_url": "relative/path",
            })
            store.get_robot_credentials_meta = AsyncMock(return_value={"robot_id": "bot-1"})
            secret.get_api_key = AsyncMock(return_value="key-123")
            with pytest.raises(AuthSetupError, match="Auth URL must be absolute"):
                await load_auth_context()


class TestLoadAuthContextSuccess:
    async def test_returns_context_from_stored_credentials(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={"base_url": "http://hub.test"})
            store.get_robot_credentials_meta = AsyncMock(return_value={
                "robot_id": "bot-1",
                "notes": "test bot",
            })
            secret.get_api_key = AsyncMock(return_value="secret-key")
            ctx = await load_auth_context()

        assert ctx.base_url == "http://hub.test"
        assert ctx.auth_url == "http://hub.test/auth/robot"
        assert ctx.robot_id == "bot-1"
        assert ctx.api_key == "secret-key"
        assert ctx.notes == "test bot"

    async def test_credentials_override_takes_precedence(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={"base_url": "http://hub"})
            store.get_robot_credentials_meta = AsyncMock(return_value={
                "robot_id": "stored-bot",
            })
            secret.get_api_key = AsyncMock(return_value="stored-key")
            ctx = await load_auth_context(credentials_override={
                "robot_id": "override-bot",
                "api_key": "override-key",
                "notes": "overridden",
            })

        assert ctx.robot_id == "override-bot"
        assert ctx.api_key == "override-key"
        assert ctx.notes == "overridden"

    async def test_override_robot_id_but_stored_api_key(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={"base_url": "http://hub"})
            store.get_robot_credentials_meta = AsyncMock(return_value=None)
            secret.get_api_key = AsyncMock(return_value="stored-key")
            ctx = await load_auth_context(credentials_override={"robot_id": "bot-x"})

        assert ctx.robot_id == "bot-x"
        assert ctx.api_key == "stored-key"

    async def test_custom_auth_url_from_config(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={
                "base_url": "http://hub",
                "auth_url": "https://auth.custom.io/token",
            })
            store.get_robot_credentials_meta = AsyncMock(return_value={"robot_id": "b1"})
            secret.get_api_key = AsyncMock(return_value="k1")
            ctx = await load_auth_context()

        assert ctx.auth_url == "https://auth.custom.io/token"

    async def test_auth_url_stripped_of_whitespace(self):
        with patch("app.core.auth_service.hub_state_store") as store, \
             patch("app.core.auth_service.robot_secret_store") as secret:
            store.get_hub_config = AsyncMock(return_value={
                "base_url": "http://hub",
                "auth_url": "  http://auth.test/token  ",
            })
            store.get_robot_credentials_meta = AsyncMock(return_value={"robot_id": "b"})
            secret.get_api_key = AsyncMock(return_value="k")
            ctx = await load_auth_context()

        assert ctx.auth_url == "http://auth.test/token"


# ── request_robot_token ───────────────────────────────────────


class TestRequestRobotToken:
    async def test_successful_token_request(self):
        ctx = AuthContext(
            base_url="http://hub",
            auth_url="http://hub/auth/robot",
            robot_id="bot-1",
            api_key="key-1",
            notes=None,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "jwt-xyz", "expires_in": 3600}
        mock_resp.request = MagicMock()
        client = MagicMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        data = await request_robot_token(client, ctx)
        assert data["access_token"] == "jwt-xyz"
        assert data["expires_in"] == 3600
        # Verify correct payload sent
        client.post.assert_awaited_once()
        call_kwargs = client.post.call_args
        assert call_kwargs.kwargs["json"] == {"robot_id": "bot-1", "api_key": "key-1"}

    async def test_raises_on_4xx(self):
        ctx = AuthContext("http://h", "http://h/auth", "r", "k", None)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.request = MagicMock()
        client = MagicMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await request_robot_token(client, ctx)

    async def test_raises_on_5xx(self):
        ctx = AuthContext("http://h", "http://h/auth", "r", "k", None)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.request = MagicMock()
        client = MagicMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(httpx.HTTPStatusError):
            await request_robot_token(client, ctx)

    async def test_raises_when_access_token_missing(self):
        ctx = AuthContext("http://h", "http://h/auth", "r", "k", None)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token_type": "bearer"}  # no access_token
        mock_resp.request = MagicMock()
        client = MagicMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Auth response missing access_token"):
            await request_robot_token(client, ctx)
