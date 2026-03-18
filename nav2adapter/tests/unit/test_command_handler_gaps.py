"""Tests for command_handler — resolve_target_config, cancel edge cases, _get_status_from_transport."""
import asyncio
import math
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.command_handler import CommandHandler
from domain.models import NavigationCommand, NavigationStatus, NavigationStatusEnum, ActiveTransport, NavigationSession, Pose2D
from services.event_bus import EventBus
from datetime import datetime


def _make_handler():
    symovo = MagicMock()
    symovo.status_uncached = AsyncMock(return_value={"state_flags": {"drive_ready": True, "halt": False, "emergency_stop": False}})
    symovo.pose = AsyncMock(return_value={"pose": {"x": 0.0, "y": 0.0}})
    symovo.transport_get = AsyncMock(return_value={"id": 42, "state": 5})
    symovo.transport_move_to_pose = AsyncMock(return_value={"id": 42, "state": 0})
    symovo.transport_start = AsyncMock(return_value={"ok": True})
    symovo.transport_stop = AsyncMock(return_value={"ok": True})
    symovo.delete_transport = AsyncMock(return_value=True)
    bus = EventBus(queue_size=100)
    handler = CommandHandler(symovo, bus, state_store=AsyncMock())
    return handler


# ── _resolve_target_config ───────────────────────────────────────────

class TestResolveTargetConfig:
    def test_finds_by_xy(self):
        h = _make_handler()
        cmd = NavigationCommand(
            command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="dock",
            x=1.0, y=2.0, theta=math.radians(90),
        )
        result = h._resolve_target_config(cmd)
        assert result is not None
        assert result.x == 1.0
        assert result.y == 2.0
        assert abs(result.theta - math.radians(90)) < 0.01

    def test_no_coords_returns_none(self):
        h = _make_handler()
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="ghost")
        result = h._resolve_target_config(cmd)
        assert result is None

    def test_empty_target_id_no_coords(self):
        h = _make_handler()
        cmd = NavigationCommand(command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="")
        result = h._resolve_target_config(cmd)
        assert result is None

    def test_station_id(self):
        h = _make_handler()
        cmd = NavigationCommand(
            command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="X",
            station_id=5,
        )
        result = h._resolve_target_config(cmd)
        assert result is not None
        assert result.station_id == 5

    def test_max_speed(self):
        h = _make_handler()
        cmd = NavigationCommand(
            command_id="c1", timestamp="2026-01-01T00:00:00Z", target_id="X",
            x=1.0, y=2.0, max_speed_m_s=0.3,
        )
        result = h._resolve_target_config(cmd)
        assert result.max_speed_m_s == 0.3


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
        h.state_store.get_active_transport = AsyncMock(return_value=None)
        h.state_store.get_all_active_commands = AsyncMock(return_value={})
        h.state_store.set_last_navigation_status = AsyncMock()
        status = await h.handle_cancel("c1")
        assert status.status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_cancel_no_active_with_other_commands(self):
        h = _make_handler()
        h.state_store.get_active_transport = AsyncMock(return_value=None)
        h.state_store.get_all_active_commands = AsyncMock(return_value={"other_cmd": MagicMock()})
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
        h.symovo_client.transport_stop = AsyncMock(side_effect=asyncio.TimeoutError)
        h.state_store.get_active_transport = AsyncMock(return_value=transport)
        h.state_store.clear_session = AsyncMock()
        h.state_store.clear_transport = AsyncMock()
        h.state_store.set_last_navigation_status = AsyncMock()
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
        assert pose is None

    @pytest.mark.asyncio
    async def test_non_dict_returns_none(self):
        h = _make_handler()
        h.symovo_client.pose = AsyncMock(return_value="string")
        pose = await h._fetch_start_pose_from_symovo()
        assert pose is None
