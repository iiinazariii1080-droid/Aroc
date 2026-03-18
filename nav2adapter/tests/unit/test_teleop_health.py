"""Tests for teleop server lifecycle in lifespan."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI


class TestTeleopLifecycle:
    @pytest.mark.asyncio
    async def test_teleop_thread_started_when_enabled(self):
        """When teleop is enabled, a daemon thread is started for teleop server."""
        from main import lifespan

        app = FastAPI()
        with patch("main.startup", new_callable=AsyncMock), \
             patch("main.shutdown", new_callable=AsyncMock), \
             patch("main.settings") as s, \
             patch("main.threading") as mock_threading:
            s.teleop_enabled = True
            s.teleop_host = "127.0.0.1"
            s.teleop_port = 7906
            s.service_host = "0.0.0.0"
            s.service_port = 7905
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread
            async with lifespan(app):
                mock_thread.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_teleop_thread_not_started_when_disabled(self):
        """When teleop is disabled, no thread is started."""
        from main import lifespan

        app = FastAPI()
        with patch("main.startup", new_callable=AsyncMock), \
             patch("main.shutdown", new_callable=AsyncMock), \
             patch("main.settings") as s, \
             patch("main.threading") as mock_threading:
            s.teleop_enabled = False
            s.service_host = "0.0.0.0"
            s.service_port = 7905
            async with lifespan(app):
                mock_threading.Thread.assert_not_called()


class TestReadyzBasic:
    def test_readyz_returns_ok(self, test_client):
        """Basic /readyz returns ok when app is healthy."""
        resp = test_client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
