"""Tests for command_handler — resolve_target_config, cancel edge cases, _get_status_from_transport."""
import asyncio
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.command_handler import CommandHandler
from domain.models import NavigationCommand, NavigationStatus, NavigationStatusEnum, ActiveTransport, NavigationSession, Pose2D
from services.event_bus import EventBus
from datetime import datetime


def _make_handler():
    symovo = MagicMock()
    symovo.status_uncached = AsyncMock(return_value={"state_flags": {"drive_ready": True, "halt": False, "emergency_stop": False}})
    symovo.pose = AsyncMock(return_value={"pose": {"x": 0.0, "y": 0.0}})
    symovo.transport_get = AsyncMock(return_value={"id": 42, "state": 5})
    orch = MagicMock()
    orch.create_transport_to_pose = AsyncMock(return_value={"id": 42, "state": 0})
    orch.start_transport = AsyncMock(return_value={"ok": True})
    orch.stop_transport = AsyncMock(return_value={"ok": True})
    orch.delete_transport = AsyncMock(return_value=True)
    mqtt = MagicMock()
    mqtt.is_connected = True
    mqtt.publish_navigation_status = AsyncMock()
    bus = EventBus(queue_size=100)
    handler = CommandHandler(symovo, orch, mqtt, bus)
    return handler


# ── _resolve_target_config ───────────────────────────────────────────

class TestResolveTargetConfig:
    @pytest.mark.asyncio
    async def test_finds_by_name(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "dock", "params": {"location": {"x_m": 1.0, "y_m": 2.0, "theta_deg": 90}}}):
            result = await h._resolve_target_config("dock")
        assert result is not None
        assert result["x"] == 1.0
        assert result["y"] == 2.0
        assert abs(result["theta"] - math.radians(90)) < 0.01

    @pytest.mark.asyncio
    async def test_not_found(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name", return_value=None):
            result = await h._resolve_target_config("ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_target_id(self):
        h = _make_handler()
        result = await h._resolve_target_config("")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_timeout(self):
        h = _make_handler()
        async def slow_lookup(_):
            await asyncio.sleep(10)
        with patch("services.command_handler.get_robot_position_by_name", slow_lookup):
            with patch("services.command_handler.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                result = await h._resolve_target_config("timeout_target")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_exception(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name", side_effect=RuntimeError("db broken")):
            with patch("services.command_handler.asyncio.to_thread", side_effect=RuntimeError("db broken")):
                result = await h._resolve_target_config("broken")
        assert result is None

    @pytest.mark.asyncio
    async def test_params_not_dict(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": "not_dict"}):
            result = await h._resolve_target_config("X")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_xy_returns_none(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"station_id": 5}}):
            result = await h._resolve_target_config("X")
        assert result is None

    @pytest.mark.asyncio
    async def test_theta_rad_preference(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"location": {"x_m": 1.0, "y_m": 2.0, "theta_rad": 1.5}}}):
            result = await h._resolve_target_config("X")
        assert result["theta"] == 1.5

    @pytest.mark.asyncio
    async def test_theta_plain_fallback(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"location": {"x_m": 1.0, "y_m": 2.0, "theta": 0.7}}}):
            result = await h._resolve_target_config("X")
        assert result["theta"] == 0.7

    @pytest.mark.asyncio
    async def test_car_position_key(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"car_position": {"x_m": 3.0, "y_m": 4.0}}}):
            result = await h._resolve_target_config("X")
        assert result["x"] == 3.0

    @pytest.mark.asyncio
    async def test_flat_xy_fallback(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"x_m": 5.0, "y_m": 6.0, "theta_deg": 0}}):
            result = await h._resolve_target_config("X")
        assert result["x"] == 5.0

    @pytest.mark.asyncio
    async def test_max_speed(self):
        h = _make_handler()
        with patch("services.command_handler.get_robot_position_by_name",
                    return_value={"id": "p1", "name": "X", "params": {"location": {"x_m": 1.0, "y_m": 2.0}, "max_speed_m_s": 0.3}}):
            result = await h._resolve_target_config("X")
        assert result["max_speed_m_s"] == 0.3


# ── _get_status_from_transport ───────────────────────────────────────

class TestGetStatusFromTransport:
    @pytest.mark.asyncio
    async def test_returns_status(self):
        h = _make_handler()
        t = MagicMock()
        t.transport_id = "42"
        t.command_id = "c1"
        h.symovo_client.transport_get = AsyncMock(return_value={"state": 5})
        status = await h._get_status_from_transport(t)
        assert status.goal_id == "c1"

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        h = _make_handler()
        t = MagicMock()
        t.transport_id = "42"
        t.command_id = "c1"
        h.symovo_client.transport_get = AsyncMock(side_effect=RuntimeError("fail"))
        status = await h._get_status_from_transport(t)
        assert status.status == NavigationStatusEnum.NAVIGATING


# ── handle_cancel edge cases ─────────────────────────────────────────

class TestHandleCancelEdge:
    @pytest.mark.asyncio
    async def test_cancel_no_active_transport(self):
        h = _make_handler()
        with patch("services.command_handler.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=None)
            ss.get_all_active_commands = AsyncMock(return_value={})
            ss.set_last_navigation_status = AsyncMock()
            status = await h.handle_cancel("c1")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_no_active_with_other_commands(self):
        h = _make_handler()
        with patch("services.command_handler.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=None)
            ss.get_all_active_commands = AsyncMock(return_value={"other_cmd": MagicMock()})
            status = await h.handle_cancel("c1")
        # Should still be IDLE but without publishing (to not overwrite other cmd's status)
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_stop_timeout(self):
        h = _make_handler()
        transport = MagicMock()
        transport.transport_id = "42"
        transport.command_id = "c1"
        transport.target_id = "dock"
        h.transport_orchestrator.stop_transport = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("services.command_handler.state_store") as ss, \
             patch("services.command_handler.charger_workflow") as cw:
            ss.get_active_transport = AsyncMock(return_value=transport)
            ss.clear_session = AsyncMock()
            ss.clear_transport = AsyncMock()
            ss.set_last_navigation_status = AsyncMock()
            cw.maybe_deactivate_on_cancel = AsyncMock()
            status = await h.handle_cancel("c1")
        # Should succeed even though stop timed out
        assert status.status == NavigationStatusEnum.IDLE


# ── _fetch_start_pose_from_symovo ────────────────────────────────────

class TestFetchStartPose:
    @pytest.mark.asyncio
    async def test_dict_pose(self):
        h = _make_handler()
        h.symovo_client.pose = AsyncMock(return_value={"pose": {"x": 1.0, "y": 2.0}})
        pose = await h._fetch_start_pose_from_symovo()
        assert pose.x == 1.0

    @pytest.mark.asyncio
    async def test_list_pose(self):
        h = _make_handler()
        h.symovo_client.pose = AsyncMock(return_value=[{"pose": {"x": 3.0, "y": 4.0}}])
        pose = await h._fetch_start_pose_from_symovo()
        assert pose.x == 3.0

    @pytest.mark.asyncio
    async def test_empty_list(self):
        h = _make_handler()
        h.symovo_client.pose = AsyncMock(return_value=[])
        pose = await h._fetch_start_pose_from_symovo()
        assert pose.x == 0.0

    @pytest.mark.asyncio
    async def test_non_dict_returns_default(self):
        h = _make_handler()
        h.symovo_client.pose = AsyncMock(return_value="string")
        pose = await h._fetch_start_pose_from_symovo()
        assert pose.x == 0.0
