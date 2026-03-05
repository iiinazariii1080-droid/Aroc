"""Integration tests for symovo_agv and aehub routes — cover major endpoints."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager

from main import app
from app.dependencies import get_symovo_client
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
        with patch("routes.symovo_agv.state_store") as ss, \
             patch("routes.symovo_agv.settings") as s, \
             _override_symovo(mock_client):
            ss.get_last_raw_pose = AsyncMock(return_value=raw)
            ss.get_last_raw_pose_age_s = AsyncMock(return_value=0.1)
            s.cache_max_age_s = 5.0
            resp = test_client.get("/pose")
        assert resp.status_code == 200

    def test_get_pose_cache_miss_fallback(self, test_client):
        """Cache miss triggers direct client call."""
        raw = {"pose": {"x": 3.0, "y": 4.0, "theta": 0.5}, "id": 1}
        mock_client = MagicMock()
        mock_client.pose = AsyncMock(return_value=raw)
        with patch("routes.symovo_agv.state_store") as ss, \
             patch("routes.symovo_agv.settings") as s, \
             _override_symovo(mock_client):
            ss.get_last_raw_pose = AsyncMock(return_value=None)
            ss.get_last_raw_pose_age_s = AsyncMock(return_value=999)
            ss.set_last_raw_pose = AsyncMock()
            s.cache_max_age_s = 5.0
            resp = test_client.get("/pose")
        assert resp.status_code == 200

    def test_get_status_from_cache(self, test_client):
        raw = {
            "id": 1,
            "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "state_flags": {"drive_ready": True},
        }
        mock_client = MagicMock()
        with patch("routes.symovo_agv.state_store") as ss, \
             patch("routes.symovo_agv.settings") as s, \
             _override_symovo(mock_client):
            ss.get_last_raw_status = AsyncMock(return_value=raw)
            ss.get_last_raw_status_age_s = AsyncMock(return_value=0.1)
            s.cache_max_age_s = 5.0
            resp = test_client.get("/status")
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

    def test_get_transport(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_get = AsyncMock(return_value={"id": 42, "state": 5})
        with _override_symovo(mock_client):
            resp = test_client.get("/transport/42")
        assert resp.status_code == 200

    def test_get_map(self, test_client):
        mock_client = MagicMock()
        mock_client.map = AsyncMock(return_value=[{"id": 0, "name": "default"}])
        with _override_symovo(mock_client):
            resp = test_client.get("/map")
        assert resp.status_code == 200


# ── aehub routes (prefix /api/v1) ───────────────────────────────────

class TestAehubRoutes:
    def test_get_robots(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            resp = test_client.get("/api/v1/robots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["robots"][0]["id"] == "test-robot"

    def test_get_positions(self, test_client):
        import asyncio
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=[
                 {"id": "p1", "name": "Pos1", "params": {"location": {"x_m": 1}}}
             ]):
            s.robot_id = "test-robot"
            resp = test_client.get("/api/v1/robots/test-robot/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["positions"]) >= 1

    def test_get_navigation_status(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.state_store") as ss:
            s.robot_id = "test-robot"
            ss.get_last_navigation_status = AsyncMock(
                return_value=NavigationStatus(
                    status=NavigationStatusEnum.IDLE,
                    goal_id=None,
                    progress_percent=0,
                )
            )
            resp = test_client.get("/api/v1/robots/test-robot/status/navigation")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_get_position_status(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.state_store") as ss:
            s.robot_id = "test-robot"
            ss.get_last_position_status = AsyncMock(
                return_value=PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
            )
            resp = test_client.get("/api/v1/robots/test-robot/status/position")
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
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub._get_poll_queue", new_callable=AsyncMock) as gpq, \
             patch("routes.aehub.event_bus") as eb:
            s.robot_id = "test-robot"
            gpq.return_value = MagicMock()
            eb.drain = AsyncMock(return_value=[])
            resp = test_client.get("/api/v1/robots/test-robot/events/poll")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data

    def test_send_navigate_command_no_facade(self, test_client):
        """When navigation_facade is absent on app.state, expect 503."""
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            # The fake startup does not set navigation_facade → 503
            resp = test_client.post(
                "/api/v1/robots/test-robot/commands/navigateTo",
                json={"target_id": "TestPose"},
            )
        assert resp.status_code == 503

    def test_send_navigate_command_success(self, test_client):
        """With a mocked facade, navigateTo should return 200."""
        mock_facade = AsyncMock()
        mock_facade.send_navigate_to = AsyncMock(
            return_value=MagicMock(topic="nav/cmd", payload={"target": "A"})
        )
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/test-robot/commands/navigateTo",
                    json={"target_id": "TestPose"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 200

    def test_send_cancel_command_no_facade(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            resp = test_client.post(
                "/api/v1/robots/test-robot/commands/cancel",
                json={"command_id": "cmd-001"},
            )
        assert resp.status_code == 503

    def test_move_speed(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "test-robot"
            tc.linear_speed = 0.3
            tc.angular_speed = 0.5
            tc.duration = 0.2
            # move_speed reads from request.app.state.symovo_client
            old_client = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = mock_symovo
            try:
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"speed": 0.1, "angular_speed": 0.2, "duration": 0.5},
                )
            finally:
                app.state.symovo_client = old_client
        assert resp.status_code == 200

    def test_move_speed_no_symovo(self, test_client):
        """When symovo_client is None on app.state, expect 503."""
        with patch("routes.aehub.settings") as s:
            s.robot_id = "test-robot"
            old = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = None
            try:
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"speed": 0.1},
                )
            finally:
                app.state.symovo_client = old
        assert resp.status_code == 503

    def test_move_speed_direction_only(self, test_client):
        """Direction-only command uses teleop_config defaults."""
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "test-robot"
            tc.linear_speed = 0.3
            tc.angular_speed = 0.5
            tc.duration = 0.2
            old = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = mock_symovo
            try:
                resp = test_client.put(
                    "/api/v1/robots/test-robot/move/speed",
                    json={"linear_dir": 1, "angular_dir": -1},
                )
            finally:
                app.state.symovo_client = old
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

    def test_pause_start(self, test_client):
        mock_client = MagicMock()
        mock_client.pause_start = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/pause/start")
        assert resp.status_code == 200

    def test_reset_emergency_stop(self, test_client):
        mock_client = MagicMock()
        mock_client.reset_emergency_stop = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/safety/reset_emergency_stop")
        assert resp.status_code == 200

    def test_reset_software_fuse(self, test_client):
        mock_client = MagicMock()
        mock_client.reset_software_fuse = AsyncMock(return_value={"ok": True})
        with _override_symovo(mock_client):
            resp = test_client.put("/safety/reset_software_fuse")
        assert resp.status_code == 200

    def test_go_to_charging_station(self, test_client):
        mock_client = MagicMock()
        mock_client.set_charging_station_enabled = AsyncMock(return_value={"activated": True})
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_charging_station/5")
        assert resp.status_code == 200

    def test_go_to_pose(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(return_value={"id": 99, "state": 0})
        with _override_symovo(mock_client):
            resp = test_client.post("/go_to_pose", json={
                "x_m": 1.0, "y_m": 2.0, "theta_deg": 90.0,
            })
        assert resp.status_code == 200

    def test_go_to_pose_with_wait(self, test_client):
        mock_client = MagicMock()
        mock_client.transport_move_to_pose = AsyncMock(return_value={
            "id": 99, "state": 8, "_wait_transport_id": 99,
        })
        mock_client._poll_transport_completion = AsyncMock(return_value={"id": 99, "state": 8})
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
