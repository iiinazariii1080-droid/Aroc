"""Tests for main.py — lifespan, heartbeat, static file routes."""
import asyncio
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── _heartbeat_loop ──────────────────────────────────────────────────

class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_updates_timestamp(self):
        from main import _heartbeat_loop
        app = FastAPI()
        app.state.last_heartbeat_ts = 0.0
        task = asyncio.create_task(_heartbeat_loop(app))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert app.state.last_heartbeat_ts > 0.0


# ── lifespan ─────────────────────────────────────────────────────────

class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_sets_startup_ok(self):
        from main import lifespan
        app = FastAPI()

        with patch("main.startup", new_callable=AsyncMock) as mock_start, \
             patch("main.shutdown", new_callable=AsyncMock) as mock_shut, \
             patch("main.settings") as s:
            s.teleop_enabled = False
            s.service_host = "0.0.0.0"
            s.service_port = 7905
            async with lifespan(app):
                assert app.state.startup_ok is True
                assert hasattr(app.state, "_heartbeat_task")
                mock_start.assert_called_once_with(app)

            # After yield — shutdown
            mock_shut.assert_called_once_with(app)

    @pytest.mark.asyncio
    async def test_lifespan_starts_teleop_thread(self):
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
    async def test_lifespan_cancels_heartbeat_on_exit(self):
        from main import lifespan
        app = FastAPI()

        with patch("main.startup", new_callable=AsyncMock), \
             patch("main.shutdown", new_callable=AsyncMock), \
             patch("main.settings") as s:
            s.teleop_enabled = False
            s.service_host = "0.0.0.0"
            s.service_port = 7905
            async with lifespan(app):
                hb_task = app.state._heartbeat_task
                assert not hb_task.done()

            # After context exit, heartbeat should be cancelled
            assert hb_task.cancelled() or hb_task.done()


# ── static file serving ──────────────────────────────────────────────

class TestStaticRoutes:
    def test_dashboard_not_found(self, test_client):
        # Patch STATIC_DIR to a non-existent file
        with patch("main.STATIC_DIR", "/tmp/__nonexistent_static_dir__"):
            resp = test_client.get("/dashboard.html")
        assert resp.status_code == 404

    def test_map_viewer_not_found(self, test_client):
        with patch("main.STATIC_DIR", "/tmp/__nonexistent_static_dir__"):
            resp = test_client.get("/map_viewer.html")
        assert resp.status_code == 404

    def test_dashboard_exists(self, test_client, tmp_path):
        html_file = tmp_path / "dashboard.html"
        html_file.write_text("<html>dash</html>")
        with patch("main.STATIC_DIR", str(tmp_path)):
            resp = test_client.get("/dashboard.html")
        assert resp.status_code == 200

    def test_map_viewer_exists(self, test_client, tmp_path):
        html_file = tmp_path / "map_viewer.html"
        html_file.write_text("<html>map</html>")
        with patch("main.STATIC_DIR", str(tmp_path)):
            resp = test_client.get("/map_viewer.html")
        assert resp.status_code == 200
