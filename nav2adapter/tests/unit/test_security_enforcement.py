"""Tests for security enforcement — API key auth on mutation endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.dependencies import require_command_auth


# ── require_command_auth dependency ───────────────────────────────


class TestRequireCommandAuth:
    @pytest.mark.asyncio
    async def test_rejects_missing_key(self):
        """Requests without X-API-Key header get 401."""
        from fastapi import HTTPException
        with patch.object(settings, "command_api_key_disabled", False), \
             patch.object(settings, "command_api_key", "correct-key"):
            with pytest.raises(HTTPException) as exc_info:
                await require_command_auth(api_key=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_wrong_key(self):
        """Requests with incorrect X-API-Key get 401."""
        from fastapi import HTTPException
        with patch.object(settings, "command_api_key_disabled", False), \
             patch.object(settings, "command_api_key", "correct-key"):
            with pytest.raises(HTTPException) as exc_info:
                await require_command_auth(api_key="wrong-key")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_accepts_correct_key(self):
        """Requests with correct X-API-Key pass auth."""
        with patch.object(settings, "command_api_key_disabled", False), \
             patch.object(settings, "command_api_key", "correct-key"):
            result = await require_command_auth(api_key="correct-key")
            assert result is None

    @pytest.mark.asyncio
    async def test_bypassed_when_disabled(self):
        """Auth is skipped when COMMAND_API_KEY_DISABLED=true."""
        with patch.object(settings, "command_api_key_disabled", True), \
             patch.object(settings, "command_api_key", "some-key"):
            result = await require_command_auth(api_key=None)
            assert result is None

    @pytest.mark.asyncio
    async def test_auth_enabled_when_not_disabled(self):
        """When command_api_key_disabled=False and key is set, auth is enforced."""
        from fastapi import HTTPException
        with patch.object(settings, "command_api_key_disabled", False), \
             patch.object(settings, "command_api_key", "secret"):
            with pytest.raises(HTTPException) as exc_info:
                await require_command_auth(api_key=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_when_no_key_configured(self):
        """When no API key is configured and auth not disabled, reject with 500."""
        from fastapi import HTTPException
        with patch.object(settings, "command_api_key_disabled", False), \
             patch.object(settings, "command_api_key", None):
            with pytest.raises(HTTPException) as exc_info:
                await require_command_auth(api_key=None)
            assert exc_info.value.status_code == 500
            assert "not configured" in exc_info.value.detail.lower()


# ── Endpoint-level auth enforcement via TestClient ────────────────


class TestEndpointAuth:
    @pytest.fixture
    def auth_client(self):
        """TestClient with API key enforcement active."""
        from fastapi.testclient import TestClient
        from main import app

        async def _fake_startup(a):
            from services.event_bus import EventBus
            from services.event_stream_service import EventStreamService

            mock_store = MagicMock()
            mock_store.get_last_navigation_status = AsyncMock(return_value=None)
            mock_bus = EventBus(queue_size=100)
            mock_event_stream = EventStreamService(mock_bus)

            services_mock = MagicMock()
            services_mock.status_publisher = MagicMock(_running=True)
            # mqtt_adapter removed
            services_mock.state_store = mock_store
            services_mock.event_bus = mock_bus
            services_mock.event_stream = mock_event_stream
            services_mock.command_handler = MagicMock()
            services_mock.symovo_client = MagicMock()

            a.state.services = services_mock
            a.state.status_publisher = MagicMock(_running=True)
            a.state.symovo_client = MagicMock()

        async def _fake_shutdown(a):
            pass

        with patch("main.startup", new=_fake_startup), \
             patch("main.shutdown", new=_fake_shutdown), \
             patch.object(settings, "teleop_enabled", False), \
             patch.object(settings, "command_api_key", "test-secret"), \
             patch.object(settings, "command_api_key_disabled", False):
            with TestClient(app) as client:
                yield client

    def test_drive_to_position_rejects_no_key(self, auth_client):
        resp = auth_client.post(
            f"/api/v1/robots/{settings.robot_id}/commands/driveToPosition",
            json={"target_id": "pos_A"},
        )
        assert resp.status_code == 401

    def test_drive_to_position_rejects_wrong_key(self, auth_client):
        resp = auth_client.post(
            f"/api/v1/robots/{settings.robot_id}/commands/driveToPosition",
            json={"target_id": "pos_A"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_cancel_rejects_no_key(self, auth_client):
        resp = auth_client.post(
            f"/api/v1/robots/{settings.robot_id}/commands/cancel",
            json={},
        )
        assert resp.status_code == 401
