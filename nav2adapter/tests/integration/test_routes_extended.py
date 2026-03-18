"""Integration tests for symovo_agv and aehub routes — cover major endpoints."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager

from fastapi import HTTPException
from main import app
from app.dependencies import (
    get_symovo_client,
    get_state_store,
    get_event_bus,
    get_event_stream,
    get_command_handler,
)
from domain.models import NavigationStatus, NavigationStatusEnum, PositionStatus


@contextmanager
def _override_symovo(mock_client):
    """Temporarily override get_symovo_client DI dependency."""
    app.dependency_overrides[get_symovo_client] = lambda: mock_client
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_symovo_client, None)


# ── symovo_agv routes (no prefix — mounted at root) ─────────────────

class TestSymovoAgvRoutes:
    def test_get_pose_from_cache(self, test_client):
        raw = {"pose": {"x": 1.0, "y": 2.0, "theta": 0.0}, "id": 1}
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_pose = AsyncMock(return_value=raw)
        mock_store.get_last_raw_pose_age_s = AsyncMock(return_value=0.1)
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.symovo_agv.settings") as s, \
                 _override_symovo(mock_client):
                s.cache_max_age_s = 5.0
                resp = test_client.get("/pose")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 200

    def test_get_pose_cache_miss_fallback(self, test_client):
        """Cache miss triggers direct client call."""
        raw = {"pose": {"x": 3.0, "y": 4.0, "theta": 0.5}, "id": 1}
        mock_client = MagicMock()
        mock_client.pose = AsyncMock(return_value=raw)
        mock_store = MagicMock()
        mock_store.get_last_raw_pose = AsyncMock(return_value=None)
        mock_store.get_last_raw_pose_age_s = AsyncMock(return_value=999)
        mock_store.set_last_raw_pose = AsyncMock()
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.symovo_agv.settings") as s, \
                 _override_symovo(mock_client):
                s.cache_max_age_s = 5.0
                resp = test_client.get("/pose")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 200

    def test_get_status_from_cache(self, test_client):
        raw = {
            "id": 1,
            "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "state_flags": {"drive_ready": True},
        }
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.get_last_raw_status = AsyncMock(return_value=raw)
        mock_store.get_last_raw_status_age_s = AsyncMock(return_value=0.1)
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.symovo_agv.settings") as s, \
                 _override_symovo(mock_client):
                s.cache_max_age_s = 5.0
                resp = test_client.get("/status")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 200

    def test_set_drive_mode(self, test_client):
        mock_client = MagicMock()
        mock_client.set_drive_mode = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/drive_mode?enable=true")
        assert resp.status_code == 200

    def test_fault_reset(self, test_client):
        mock_client = MagicMock()
        mock_client.clear_all_transports = AsyncMock(return_value=[])
        with _override_symovo(mock_client):
            resp = test_client.post("/fault_reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_get_charging_stations(self, test_client):
        mock_client = MagicMock()
        mock_client.get_charging_stations = AsyncMock(return_value=[{"id": 1, "state": "INACTIVE"}])
        with _override_symovo(mock_client):
            resp = test_client.get("/charging_stations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stations"][0]["id"] == 1

    def test_get_transport(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_get = AsyncMock(return_value={"id": 42, "state": 5})
        with _override_symovo(mock_client):
            resp = test_client.get("/transport/42")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 42
        assert data["state"] == 5

    def test_get_map(self, test_client):
        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value=[{"id": 0, "name": "default"}])
        with _override_symovo(mock_client):
            resp = test_client.get("/map")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["maps"], list)

    def test_get_pose_no_state_store_returns_503(self, test_client):
        """When StateStore DI fails, /pose returns 503, not AttributeError."""
        def _no_store():
            raise HTTPException(status_code=503, detail="StateStore not available")
        app.dependency_overrides[get_state_store] = _no_store
        try:
            resp = test_client.get("/pose")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 503

    def test_get_status_no_state_store_returns_503(self, test_client):
        """When StateStore DI fails, /status returns 503, not AttributeError."""
        def _no_store():
            raise HTTPException(status_code=503, detail="StateStore not available")
        app.dependency_overrides[get_state_store] = _no_store
        try:
            resp = test_client.get("/status")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 503


# ── aehub routes (prefix /api/v1) ───────────────────────────────────

class TestAehubRoutes:
    def test_get_robots(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            resp = test_client.get("/api/v1/robots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["robots"][0]["id"] == "test-robot"

    def test_get_navigation_status(self, test_client):
        mock_store = AsyncMock()
        mock_store.get_last_navigation_status = AsyncMock(
            return_value=NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
            )
        )
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.get("/api/v1/robots/test-robot/status/navigation")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_get_position_status(self, test_client):
        mock_store = AsyncMock()
        mock_store.get_last_position_status = AsyncMock(
            return_value=PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        )
        app.dependency_overrides[get_state_store] = lambda: mock_store
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.get("/api/v1/robots/test-robot/status/position")
        finally:
            app.dependency_overrides.pop(get_state_store, None)
        assert resp.status_code == 200
        data = resp.json()
        assert data["x"] == 1.0

    def test_get_teleop_config(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "test-robot"
            tc.snapshot.return_value = {
                "duration": 0.2,
                "linear_m_s": 0.3,
                "angular_rad_s": 0.5,
            }
            resp = test_client.get("/api/v1/robots/test-robot/teleop/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "duration" in data

    def test_poll_events(self, test_client):
        import asyncio
        from services.event_stream_service import EventStreamService
        from services.event_bus import EventBus
        mock_event_stream = MagicMock(spec=EventStreamService)
        mock_event_stream.get_poll_queue = AsyncMock(return_value=asyncio.Queue())
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.drain = AsyncMock(return_value=[])
        app.dependency_overrides[get_event_stream] = lambda: mock_event_stream
        app.dependency_overrides[get_event_bus] = lambda: mock_bus
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.get("/api/v1/robots/test-robot/events/poll")
        finally:
            app.dependency_overrides.pop(get_event_stream, None)
            app.dependency_overrides.pop(get_event_bus, None)
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data

    def test_send_drive_to_position_no_handler(self, test_client):
        """When command_handler is absent on app.state, expect 503."""
        def _no_handler():
            raise HTTPException(status_code=503, detail="CommandHandler not available")
        app.dependency_overrides[get_command_handler] = _no_handler
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.post(
                    "/api/v1/robots/test-robot/commands/driveToPosition",
                    json={"target_id": "TestPose"},
                )
        finally:
            app.dependency_overrides.pop(get_command_handler, None)
        assert resp.status_code == 503

    def test_send_drive_to_position_success(self, test_client):
        """With a mocked handler, driveToPosition should return 200."""
        mock_handler = AsyncMock()
        mock_handler.handle_drive_to_position = AsyncMock(
            return_value=NavigationStatus(
                status=NavigationStatusEnum.NAVIGATING,
                goal_id="cmd-1",
                progress_percent=1,
            )
        )
        app.dependency_overrides[get_command_handler] = lambda: mock_handler
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.post(
                    "/api/v1/robots/test-robot/commands/driveToPosition",
                    json={"target_id": "TestPose"},
                )
        finally:
            app.dependency_overrides.pop(get_command_handler, None)
        assert resp.status_code == 200
        data = resp.json()
        assert "payload" in data
        assert data["payload"]["navigation_status"]["status"] == "navigating"

    def test_send_cancel_command_no_handler(self, test_client):
        """When command_handler is absent, expect 503."""
        def _no_handler():
            raise HTTPException(status_code=503, detail="CommandHandler not available")
        app.dependency_overrides[get_command_handler] = _no_handler
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.post(
                    "/api/v1/robots/test-robot/commands/cancel",
                    json={"command_id": "cmd-001"},
                )
        finally:
            app.dependency_overrides.pop(get_command_handler, None)
        assert resp.status_code == 503

    def test_move_speed(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})

        async def _override():
            yield mock_symovo

        app.dependency_overrides[get_symovo_client] = _override
        try:
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.teleop_config") as tc:
                s.robot_id = "test-robot"
                tc.linear_speed = 0.3
                tc.angular_speed = 0.5
                tc.duration = 0.2
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"speed": 0.1, "angular_speed": 0.2, "duration": 0.5},
                )
        finally:
            app.dependency_overrides.pop(get_symovo_client, None)
        assert resp.status_code == 200

    def test_move_speed_no_symovo(self, test_client):
        """When symovo_client raises an error, expect 503."""
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(side_effect=RuntimeError("no client"))

        async def _override():
            yield mock_symovo

        app.dependency_overrides[get_symovo_client] = _override
        try:
            with patch("routes.aehub.settings") as s:
                s.robot_id = "test-robot"
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"speed": 0.1},
                )
        finally:
            app.dependency_overrides.pop(get_symovo_client, None)
        assert resp.status_code == 503

    def test_move_speed_direction_only(self, test_client):
        """Direction-only command uses teleop_config defaults."""
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})

        async def _override():
            yield mock_symovo

        app.dependency_overrides[get_symovo_client] = _override
        try:
            with patch("routes.aehub.settings") as s, \
                 patch("routes.aehub.teleop_config") as tc:
                s.robot_id = "test-robot"
                tc.linear_speed = 0.3
                tc.angular_speed = 0.5
                tc.duration = 0.2
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"linear_dir": 1, "angular_dir": -1},
                )
        finally:
            app.dependency_overrides.pop(get_symovo_client, None)
        assert resp.status_code == 200

    def test_wrong_robot_id_404(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            resp = test_client.get("/api/v1/robots/wrong-robot/status/navigation")
        assert resp.status_code == 404

    def test_update_teleop_config(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "test-robot"
            tc.duration = 0.2
            tc.linear_speed = 0.3
            tc.angular_speed = 0.5
            tc.snapshot.return_value = {"duration": 0.5, "linear_m_s": 0.3, "angular_rad_s": 0.5}
            resp = test_client.put(
                "/api/v1/robots/test-robot/teleop/config",
                json={"duration": 0.5},
            )
        assert resp.status_code == 200


# ── more symovo_agv routes ───────────────────────────────────────────

class TestSymovoAgvRoutesMore:
    def test_pause_stop(self, test_client):
        mock_client = MagicMock()
        mock_client.pause_stop = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/pause/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == {"ok": True}

    def test_pause_start(self, test_client):
        mock_client = MagicMock()
        mock_client.pause_start = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/pause/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == {"ok": True}

    def test_reset_emergency_stop(self, test_client):
        mock_client = MagicMock()
        mock_client.reset_emergency_stop = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/safety/reset_emergency_stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == {"ok": True}

    def test_reset_software_fuse(self, test_client):
        mock_client = MagicMock()
        mock_client.reset_software_fuse = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/safety/reset_software_fuse")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == {"ok": True}

    def test_go_to_charging_station(self, test_client):
        mock_client = MagicMock()
        mock_client.set_charging_station_enabled = AsyncMock(return_value={"activated": True})
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_charging_station/5")
        assert resp.status_code == 200
        assert resp.json()["activated"] is True

    def test_go_to_pose(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(return_value={"id": 99, "state": 0})
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_pose", json={
                "x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0,
            })
        assert resp.status_code == 200
        assert resp.json()["id"] == 99

    def test_go_to_pose_with_wait(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(return_value={
            "id": 99, "state": 8, "_wait_transport_id": 99,
        })
        mock_client.poll_transport_completion = AsyncMock(return_value={"id": 99, "state": 8})
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_pose", json={
                "x_m": 1.0, "y_m": 2.0, "theta_deg": 0.0, "wait": True,
            })
        assert resp.status_code == 200

    def test_get_map_png(self, test_client):
        mock_client = MagicMock()
        mock_client.map_png = AsyncMock(return_value=b"\x89PNG_data_bytes")
        with _override_symovo(mock_client):
            resp = test_client.get("/map/0/full.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_map_png_error(self, test_client):
        mock_client = MagicMock()
        mock_client.map_png = AsyncMock(side_effect=RuntimeError("no map"))
        with _override_symovo(mock_client):
            resp = test_client.get("/map/0/full.png")
        assert resp.status_code == 503

    def test_transport_wait_for_changes(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_wait_for_changes = AsyncMock(return_value={"id": 42, "state": 8})
        with _override_symovo(mock_client):
            resp = test_client.get("/transport/42/wait_for_changes")
        assert resp.status_code == 200

    def test_v1_symovo_map(self, test_client):
        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value=[{"id": 0}])
        with _override_symovo(mock_client):
            resp = test_client.get("/api/v1/symovo/map")
        assert resp.status_code == 200
