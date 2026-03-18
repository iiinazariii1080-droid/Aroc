"""Extended tests for routes/aehub.py — SSE stream, poll queues, positions data shapes, command errors."""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from fastapi.testclient import TestClient
from main import app
from app.config import settings
from app.dependencies import (
    get_command_handler,
    get_symovo_client,
    get_state_store,
    get_event_bus,
    get_event_stream,
)
from domain.models import NavigationStatus, NavigationStatusEnum
from services.event_stream_service import EventStreamService
from services.event_bus import EventBus


# ── helpers ──────────────────────────────────────────────────────────

def _patch_robot(rid="test-robot"):
    return patch("routes.aehub.settings", **{"robot_id": rid})


def _override_handler(mock_handler):
    """Install a DI override for command_handler."""
    app.dependency_overrides[get_command_handler] = lambda: mock_handler
    return mock_handler


def _override_symovo(mock_client):
    """Install a DI override for symovo_client."""
    async def _gen():
        yield mock_client
    app.dependency_overrides[get_symovo_client] = _gen


def _clear_overrides():
    app.dependency_overrides.pop(get_command_handler, None)
    app.dependency_overrides.pop(get_symovo_client, None)
    app.dependency_overrides.pop(get_state_store, None)
    app.dependency_overrides.pop(get_event_bus, None)
    app.dependency_overrides.pop(get_event_stream, None)


# (TestGetPositionsExtended removed: positions list endpoint was removed with SQLite)


# ── SSE stream_events ────────────────────────────────────────────────

class TestStreamEvents:
    def test_wrong_robot_404(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/wrong/events")
        assert resp.status_code == 404


# ── poll_events ──────────────────────────────────────────────────────

class TestPollEventsExtended:
    def test_wrong_robot_404(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/wrong/events/poll")
        assert resp.status_code == 404


# ── EventStreamService poll queue management (unit-level) ────────────

class TestPollQueueManagement:
    @pytest.mark.asyncio
    async def test_get_poll_queue_creates_new(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        q = await svc.get_poll_queue("client-1")
        assert q is not None
        assert "client-1" in svc._poll_queues
        mock_bus.subscribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_poll_queue_reuses_existing(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        q = asyncio.Queue()
        svc._poll_queues["client-1"] = (q, time.monotonic())
        result = await svc.get_poll_queue("client-1")
        assert result is q

    @pytest.mark.asyncio
    async def test_get_poll_queue_max_clients_raises(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        # Fill to max
        for i in range(svc.POLL_MAX_CLIENTS):
            svc._poll_queues[f"c{i}"] = (asyncio.Queue(), time.monotonic())
        with pytest.raises(RuntimeError, match="Too many polling clients"):
            await svc.get_poll_queue("overflow")

    @pytest.mark.asyncio
    async def test_get_poll_queue_removes_stale(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        # Add a stale queue
        stale_q = asyncio.Queue()
        svc._poll_queues["stale"] = (stale_q, time.monotonic() - 130)  # > _POLL_TTL (120)
        await svc.get_poll_queue("fresh")
        assert "stale" not in svc._poll_queues
        assert "fresh" in svc._poll_queues

    @pytest.mark.asyncio
    async def test_cleanup_loop_removes_stale(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        stale_q = asyncio.Queue()
        svc._poll_queues["stale"] = (stale_q, time.monotonic() - 130)

        with patch("services.event_stream_service.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            try:
                await svc._cleanup_loop()
            except asyncio.CancelledError:
                pass
            assert "stale" not in svc._poll_queues

    @pytest.mark.asyncio
    async def test_stop_cancels_cleanup_task(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        await svc.start()
        assert svc._cleanup_task is not None
        assert not svc._cleanup_task.done()
        await svc.stop()
        assert svc._cleanup_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.unsubscribe = AsyncMock()
        svc = EventStreamService(mock_bus)
        await svc.stop()  # no-op, should not raise
        assert svc._cleanup_task is None


# ── driveToPosition command errors ──────────────────────────────────

class TestDriveToPositionExtended:
    def test_generic_error_503(self, test_client):
        mock_handler = AsyncMock()
        mock_handler.handle_drive_to_position = AsyncMock(side_effect=RuntimeError("boom"))
        _override_handler(mock_handler)
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "r"
                resp = test_client.post(
                    "/api/v1/robots/r/commands/driveToPosition",
                    json={"target_id": "X"},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 503
        assert "CommandDeliveryFailed" in resp.text


# ── cancel command errors ────────────────────────────────────────────

class TestCancelExtended:
    def test_cancel_success(self, test_client):
        mock_handler = AsyncMock()
        mock_handler.handle_cancel = AsyncMock(
            return_value=NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
            )
        )
        _override_handler(mock_handler)
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "r"
                resp = test_client.post(
                    "/api/v1/robots/r/commands/cancel",
                    json={"command_id": "c1"},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 200

    def test_cancel_generic_error(self, test_client):
        mock_handler = AsyncMock()
        mock_handler.handle_cancel = AsyncMock(side_effect=Exception("fail"))
        _override_handler(mock_handler)
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "r"
                resp = test_client.post(
                    "/api/v1/robots/r/commands/cancel",
                    json={"command_id": "c1"},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 503


# ── move/speed extended ──────────────────────────────────────────────

class TestMoveSpeedExtended:
    def test_move_speed_exception_503(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(side_effect=RuntimeError("hardware error"))
        _override_symovo(mock_symovo)
        try:
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.teleop_config") as tc:
                s.robot_id = "r"
                tc.linear_speed = 0.1
                tc.angular_speed = 0.5
                tc.duration = 0.2
                resp = test_client.put(
                    "/api/v1/robots/r/move/speed",
                    json={"speed": 0.1},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 503
        assert "MoveSpeedFailed" in resp.text

    def test_move_speed_non_dict_result(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value="not_a_dict")
        _override_symovo(mock_symovo)
        try:
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.teleop_config") as tc:
                s.robot_id = "r"
                tc.linear_speed = 0.1
                tc.angular_speed = 0.5
                tc.duration = 0.2
                resp = test_client.put(
                    "/api/v1/robots/r/move/speed",
                    json={"speed": 0.1},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_move_speed_post_method(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})
        _override_symovo(mock_symovo)
        try:
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.teleop_config") as tc:
                s.robot_id = "r"
                tc.linear_speed = 0.1
                tc.angular_speed = 0.5
                tc.duration = 0.2
                resp = test_client.post(
                    "/api/v1/robots/r/move/speed",
                    json={"linear_dir": -1},
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 200

    def test_move_speed_wrong_robot(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.put(
                "/api/v1/robots/wrong/move/speed",
                json={"speed": 0.1},
            )
        assert resp.status_code == 404


# ── teleop config extended ───────────────────────────────────────────

class TestTeleopConfigExtended:
    def test_get_wrong_robot(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/wrong/teleop/config")
        assert resp.status_code == 404

    def test_put_wrong_robot(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.put(
                "/api/v1/robots/wrong/teleop/config",
                json={"duration": 0.5},
            )
        assert resp.status_code == 404

    def test_put_updates_all_fields(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "r"
            tc.duration = 0.2
            tc.linear_speed = 0.1
            tc.angular_speed = 0.5
            tc.snapshot.return_value = {"duration": 1.0, "linear_m_s": 0.5, "angular_rad_s": 1.5}
            resp = test_client.put(
                "/api/v1/robots/r/teleop/config",
                json={"duration": 1.0, "linear_m_s": 0.5, "angular_rad_s": 1.5},
            )
        assert resp.status_code == 200


# ── navigation status default ────────────────────────────────────────

class TestNavigationStatusDefault:
    def test_returns_default_when_none(self, test_client):
        mock_store = AsyncMock()
        mock_store.get_last_navigation_status = AsyncMock(return_value=None)
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "r"
                resp = test_client.get("/api/v1/robots/r/status/navigation")
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        assert data["goal_id"] is None
        assert data["progress_percent"] == 0
