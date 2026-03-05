"""Extended tests for routes/aehub.py — SSE stream, poll queues, positions data shapes, command errors."""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from fastapi.testclient import TestClient
from main import app
from app.config import settings


# ── helpers ──────────────────────────────────────────────────────────

def _patch_robot(rid="test-robot"):
    return patch("routes.aehub.settings", **{"robot_id": rid})


# ── get_positions — data-shape edge cases ────────────────────────────

class TestGetPositionsExtended:
    def test_non_dict_rows_skipped(self, test_client):
        rows = ["string_row", 42, {"id": "p1", "name": "A", "params": {"x": 1}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["positions"]) == 1  # only dict row

    def test_missing_id_skipped(self, test_client):
        rows = [{"name": "A", "params": {}}, {"id": "", "name": "B", "params": {}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions")
        assert resp.status_code == 200
        assert len(resp.json()["positions"]) == 0

    def test_label_from_params_position_id(self, test_client):
        rows = [{"id": "p1", "name": None, "params": {"position_id": "ParkingA"}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions")
        assert resp.status_code == 200
        pos = resp.json()["positions"][0]
        assert pos["label"] == "ParkingA"

    def test_label_fallback_to_id(self, test_client):
        rows = [{"id": "p1", "name": None, "params": {"other": 1}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions")
        pos = resp.json()["positions"][0]
        assert pos["label"] == "p1"

    def test_description_from_params(self, test_client):
        rows = [{"id": "p1", "name": "X", "params": {"description": "Near dock"}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions")
        pos = resp.json()["positions"][0]
        assert pos["description"] == "Near dock"

    def test_limit_clamped_high(self, test_client):
        rows = [{"id": f"p{i}", "name": f"N{i}", "params": {}} for i in range(200)]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions?limit=999")
        # Clamped to 100
        assert len(resp.json()["positions"]) == 100

    def test_limit_clamped_low(self, test_client):
        rows = [{"id": "p1", "name": "X", "params": {}}]
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=rows):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions?limit=-5")
        # Clamped to 1
        assert resp.status_code == 200

    def test_none_rows_returns_empty(self, test_client):
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.get_robot_positions_list", return_value=None):
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/r/positions")
        assert resp.status_code == 200
        assert resp.json()["positions"] == []

    def test_wrong_robot_404(self, test_client):
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            resp = test_client.get("/api/v1/robots/wrong/positions")
        assert resp.status_code == 404


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


# ── _get_poll_queue / _poll_cleanup_loop (unit-level) ────────────────

class TestPollQueueManagement:
    @pytest.mark.asyncio
    async def test_get_poll_queue_creates_new(self):
        import routes.aehub as mod
        old_queues = mod._poll_queues.copy()
        old_task = mod._poll_cleanup_task
        try:
            mod._poll_queues.clear()
            mod._poll_cleanup_task = None
            with patch.object(mod.event_bus, "subscribe", new_callable=AsyncMock) as sub:
                sub.return_value = asyncio.Queue()
                q = await mod._get_poll_queue("client-1")
                assert q is not None
                assert "client-1" in mod._poll_queues
                sub.assert_called_once()
        finally:
            # Cleanup
            if mod._poll_cleanup_task and not mod._poll_cleanup_task.done():
                mod._poll_cleanup_task.cancel()
                try:
                    await mod._poll_cleanup_task
                except (asyncio.CancelledError, Exception):
                    pass
            mod._poll_queues.clear()
            mod._poll_queues.update(old_queues)
            mod._poll_cleanup_task = old_task

    @pytest.mark.asyncio
    async def test_get_poll_queue_reuses_existing(self):
        import routes.aehub as mod
        old_queues = mod._poll_queues.copy()
        old_task = mod._poll_cleanup_task
        try:
            mod._poll_queues.clear()
            mod._poll_cleanup_task = None
            q = asyncio.Queue()
            mod._poll_queues["client-1"] = (q, time.monotonic())
            with patch.object(mod.event_bus, "subscribe", new_callable=AsyncMock):
                result = await mod._get_poll_queue("client-1")
                assert result is q
        finally:
            if mod._poll_cleanup_task and not mod._poll_cleanup_task.done():
                mod._poll_cleanup_task.cancel()
                try:
                    await mod._poll_cleanup_task
                except (asyncio.CancelledError, Exception):
                    pass
            mod._poll_queues.clear()
            mod._poll_queues.update(old_queues)
            mod._poll_cleanup_task = old_task

    @pytest.mark.asyncio
    async def test_get_poll_queue_max_clients_429(self):
        import routes.aehub as mod
        from fastapi import HTTPException
        old_queues = mod._poll_queues.copy()
        old_task = mod._poll_cleanup_task
        try:
            mod._poll_queues.clear()
            mod._poll_cleanup_task = asyncio.Future()
            mod._poll_cleanup_task.set_result(None)
            # Fill to max
            for i in range(mod._POLL_MAX_CLIENTS):
                mod._poll_queues[f"c{i}"] = (asyncio.Queue(), time.monotonic())
            with pytest.raises(HTTPException) as exc_info:
                await mod._get_poll_queue("overflow")
            assert exc_info.value.status_code == 429
        finally:
            mod._poll_queues.clear()
            mod._poll_queues.update(old_queues)
            mod._poll_cleanup_task = old_task

    @pytest.mark.asyncio
    async def test_get_poll_queue_removes_stale(self):
        import routes.aehub as mod
        old_queues = mod._poll_queues.copy()
        old_task = mod._poll_cleanup_task
        try:
            mod._poll_queues.clear()
            mod._poll_cleanup_task = asyncio.Future()
            mod._poll_cleanup_task.set_result(None)
            # Add a stale queue
            stale_q = asyncio.Queue()
            mod._poll_queues["stale"] = (stale_q, time.monotonic() - mod._POLL_TTL - 10)
            with patch.object(mod.event_bus, "subscribe", new_callable=AsyncMock) as sub, \
                 patch.object(mod.event_bus, "unsubscribe", new_callable=AsyncMock):
                sub.return_value = asyncio.Queue()
                await mod._get_poll_queue("fresh")
                assert "stale" not in mod._poll_queues
                assert "fresh" in mod._poll_queues
        finally:
            mod._poll_queues.clear()
            mod._poll_queues.update(old_queues)
            mod._poll_cleanup_task = old_task

    @pytest.mark.asyncio
    async def test_poll_cleanup_loop_removes_stale(self):
        import routes.aehub as mod
        old_queues = mod._poll_queues.copy()
        try:
            mod._poll_queues.clear()
            stale_q = asyncio.Queue()
            mod._poll_queues["stale"] = (stale_q, time.monotonic() - mod._POLL_TTL - 10)

            with patch.object(mod.event_bus, "unsubscribe", new_callable=AsyncMock), \
                 patch("routes.aehub.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
                try:
                    await mod._poll_cleanup_loop()
                except asyncio.CancelledError:
                    pass
                assert "stale" not in mod._poll_queues
        finally:
            mod._poll_queues.clear()
            mod._poll_queues.update(old_queues)

    def test_cancel_poll_cleanup_task(self):
        import routes.aehub as mod
        old = mod._poll_cleanup_task
        fake_task = MagicMock()
        fake_task.done.return_value = False
        mod._poll_cleanup_task = fake_task
        mod.cancel_poll_cleanup_task()
        fake_task.cancel.assert_called_once()
        assert mod._poll_cleanup_task is None
        mod._poll_cleanup_task = old

    def test_cancel_poll_cleanup_task_none(self):
        import routes.aehub as mod
        old = mod._poll_cleanup_task
        mod._poll_cleanup_task = None
        mod.cancel_poll_cleanup_task()  # no-op
        assert mod._poll_cleanup_task is None
        mod._poll_cleanup_task = old


# ── navigateTo command errors ────────────────────────────────────────

class TestNavigateToExtended:
    def test_mqtt_unavailable_503(self, test_client):
        from services.mqtt_adapter import MqttUnavailableError
        mock_facade = AsyncMock()
        mock_facade.send_navigate_to = AsyncMock(side_effect=MqttUnavailableError("no mqtt"))
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/commands/navigateTo",
                    json={"target_id": "X"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 503
        assert "MQTTUnavailable" in resp.text

    def test_generic_error_503(self, test_client):
        mock_facade = AsyncMock()
        mock_facade.send_navigate_to = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/commands/navigateTo",
                    json={"target_id": "X"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 503
        assert "CommandDeliveryFailed" in resp.text


# ── cancel command errors ────────────────────────────────────────────

class TestCancelExtended:
    def test_cancel_success(self, test_client):
        mock_facade = AsyncMock()
        mock_facade.send_cancel = AsyncMock(
            return_value=MagicMock(topic="nav/cancel", payload={"command_id": "c1"})
        )
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/commands/cancel",
                    json={"command_id": "c1"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 200

    def test_cancel_mqtt_error(self, test_client):
        from services.mqtt_adapter import MqttUnavailableError
        mock_facade = AsyncMock()
        mock_facade.send_cancel = AsyncMock(side_effect=MqttUnavailableError("no mqtt"))
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/commands/cancel",
                    json={"command_id": "c1"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 503

    def test_cancel_generic_error(self, test_client):
        mock_facade = AsyncMock()
        mock_facade.send_cancel = AsyncMock(side_effect=Exception("fail"))
        with patch("routes.aehub.settings") as s:
            s.robot_id = "r"
            app.state.navigation_facade = mock_facade
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/commands/cancel",
                    json={"command_id": "c1"},
                )
            finally:
                del app.state.navigation_facade
        assert resp.status_code == 503


# ── move/speed extended ──────────────────────────────────────────────

class TestMoveSpeedExtended:
    def test_move_speed_exception_503(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(side_effect=RuntimeError("hardware error"))
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "r"
            tc.linear_speed = 0.1
            tc.angular_speed = 0.5
            tc.duration = 0.2
            old = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = mock_symovo
            try:
                resp = test_client.put(
                    "/api/v1/robots/r/move/speed",
                    json={"speed": 0.1},
                )
            finally:
                app.state.symovo_client = old
        assert resp.status_code == 503
        assert "MoveSpeedFailed" in resp.text

    def test_move_speed_non_dict_result(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value="not_a_dict")
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "r"
            tc.linear_speed = 0.1
            tc.angular_speed = 0.5
            tc.duration = 0.2
            old = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = mock_symovo
            try:
                resp = test_client.put(
                    "/api/v1/robots/r/move/speed",
                    json={"speed": 0.1},
                )
            finally:
                app.state.symovo_client = old
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_move_speed_post_method(self, test_client):
        mock_symovo = AsyncMock()
        mock_symovo.move_speed = AsyncMock(return_value={"status": "ok"})
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.teleop_config") as tc:
            s.robot_id = "r"
            tc.linear_speed = 0.1
            tc.angular_speed = 0.5
            tc.duration = 0.2
            old = getattr(app.state, "symovo_client", None)
            app.state.symovo_client = mock_symovo
            try:
                resp = test_client.post(
                    "/api/v1/robots/r/move/speed",
                    json={"linear_dir": -1},
                )
            finally:
                app.state.symovo_client = old
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
        with patch("routes.aehub.settings") as s, \
             patch("routes.aehub.state_store") as ss:
            s.robot_id = "r"
            ss.get_last_navigation_status = AsyncMock(return_value=None)
            resp = test_client.get("/api/v1/robots/r/status/navigation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        assert data["goal_id"] is None
        assert data["progress_percent"] == 0
