"""Tests for StateStore — register, clear, sessions, raw cache, persistence enqueue."""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from services.state_store import StateStore
from domain.models import NavigationStatus, NavigationStatusEnum, PositionStatus, NavigationSession, Pose2D, ActiveTransport


@pytest.fixture
def store():
    """Fresh StateStore with persistence disabled."""
    with patch("services.state_store.settings") as s:
        s.persistence_enabled = False
        s.persistence_path = None
        return StateStore()


# ── register_command ─────────────────────────────────────────────────

class TestRegisterCommand:
    @pytest.mark.asyncio
    async def test_register_and_retrieve(self, store):
        t = await store.register_command("cmd1", "t1", 0, target_id="Target")
        assert t.command_id == "cmd1"
        assert t.transport_id == "t1"
        assert t.generation == 0
        result = await store.get_active_transport("cmd1")
        assert result is not None
        assert result.transport_id == "t1"

    @pytest.mark.asyncio
    async def test_reuse_increments_generation(self, store):
        await store.register_command("cmd1", "t1", 0)
        t2 = await store.register_command("cmd1", "t2", 0)
        assert t2.generation == 1

    @pytest.mark.asyncio
    async def test_sets_current_command_id(self, store):
        await store.register_command("cmd1", "t1", 0)
        assert await store.get_current_command_id() == "cmd1"


# ── update_transport_state ───────────────────────────────────────────

class TestUpdateTransportState:
    @pytest.mark.asyncio
    async def test_update_state(self, store):
        await store.register_command("cmd1", "t1", 0)
        updated = await store.update_transport_state("cmd1", 5)
        assert updated.state == 5

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, store):
        result = await store.update_transport_state("nope", 5)
        assert result is None


# ── clear_transport ──────────────────────────────────────────────────

class TestClearTransport:
    @pytest.mark.asyncio
    async def test_clear_existing(self, store):
        await store.register_command("cmd1", "t1", 0)
        cleared = await store.clear_transport("cmd1")
        assert cleared is True
        assert await store.get_active_transport("cmd1") is None

    @pytest.mark.asyncio
    async def test_clear_missing(self, store):
        cleared = await store.clear_transport("nope")
        assert cleared is False

    @pytest.mark.asyncio
    async def test_clear_updates_current_command(self, store):
        await store.register_command("cmd1", "t1", 0)
        await store.register_command("cmd2", "t2", 0)
        await store.clear_transport("cmd2")
        current = await store.get_current_command_id()
        assert current == "cmd1"


# ── clear_all_commands ───────────────────────────────────────────────

class TestClearAllCommands:
    @pytest.mark.asyncio
    async def test_clears_everything(self, store):
        await store.register_command("cmd1", "t1", 0)
        await store.register_command("cmd2", "t2", 0)
        count = await store.clear_all_commands()
        assert count == 2
        assert await store.get_current_command_id() is None
        assert await store.get_all_active_commands() == {}


# ── sessions ─────────────────────────────────────────────────────────

class TestSessions:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, store):
        session = NavigationSession(
            command_id="cmd1",
            target_id="Target",
            start=Pose2D(x=0, y=0),
            goal=Pose2D(x=10, y=10),
            total_dist_m=14.14,
            min_remaining_dist_m=14.14,
            progress_percent=0,
        )
        await store.upsert_session(session)
        result = await store.get_session("cmd1")
        assert result is not None
        assert result.total_dist_m == 14.14

    @pytest.mark.asyncio
    async def test_clear_session(self, store):
        session = NavigationSession(
            command_id="cmd1",
            target_id="Target",
            start=Pose2D(x=0, y=0),
            goal=Pose2D(x=10, y=10),
            total_dist_m=14.14,
            min_remaining_dist_m=14.14,
            progress_percent=0,
        )
        await store.upsert_session(session)
        await store.clear_session("cmd1")
        assert await store.get_session("cmd1") is None


# ── navigation status ────────────────────────────────────────────────

class TestNavigationStatus:
    @pytest.mark.asyncio
    async def test_set_and_get(self, store):
        status = NavigationStatus(
            status=NavigationStatusEnum.NAVIGATING,
            goal_id="cmd1",
            progress_percent=50,
        )
        await store.set_last_navigation_status(status)
        result = await store.get_last_navigation_status()
        assert result.goal_id == "cmd1"
        assert result.progress_percent == 50

    @pytest.mark.asyncio
    async def test_get_with_ts(self, store):
        status = NavigationStatus(
            status=NavigationStatusEnum.IDLE,
            goal_id=None,
            progress_percent=0,
        )
        await store.set_last_navigation_status(status)
        result, ts = await store.get_last_navigation_status_with_ts()
        assert result is not None
        assert ts > 0


# ── position status ──────────────────────────────────────────────────

class TestPositionStatus:
    @pytest.mark.asyncio
    async def test_set_and_get(self, store):
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        await store.set_last_position_status(pos)
        result = await store.get_last_position_status()
        assert result.x == 1.0


# ── raw cache ────────────────────────────────────────────────────────

class TestRawCache:
    @pytest.mark.asyncio
    async def test_pose_cache(self, store):
        assert await store.get_last_raw_pose() is None
        assert await store.get_last_raw_pose_age_s() == float("inf")
        await store.set_last_raw_pose({"x": 1})
        assert await store.get_last_raw_pose() == {"x": 1}
        assert await store.get_last_raw_pose_age_s() < 1.0

    @pytest.mark.asyncio
    async def test_status_cache(self, store):
        assert await store.get_last_raw_status() is None
        assert await store.get_last_raw_status_age_s() == float("inf")
        await store.set_last_raw_status({"state": "IDLE"})
        assert await store.get_last_raw_status() == {"state": "IDLE"}
        assert await store.get_last_raw_status_age_s() < 1.0


# ── clear_all ────────────────────────────────────────────────────────

class TestClearAll:
    @pytest.mark.asyncio
    async def test_clears_all_state(self, store):
        await store.register_command("cmd1", "t1", 0)
        await store.set_last_navigation_status(
            NavigationStatus(status=NavigationStatusEnum.NAVIGATING, goal_id="cmd1", progress_percent=50)
        )
        await store.set_last_position_status(PositionStatus(x=1, y=2, theta=0, frame_id="map"))
        await store.set_last_raw_pose({"x": 1})
        await store.set_last_raw_status({"state": "IDLE"})

        await store.clear_all()

        assert await store.get_all_active_commands() == {}
        assert await store.get_last_navigation_status() is None
        assert await store.get_last_position_status() is None
        assert await store.get_last_raw_pose() is None
        assert await store.get_last_raw_status() is None


# ── get_active_transport_by_transport_id ─────────────────────────────

class TestGetByTransportId:
    @pytest.mark.asyncio
    async def test_found(self, store):
        await store.register_command("cmd1", "t-42", 0)
        result = await store.get_active_transport_by_transport_id("t-42")
        assert result is not None
        assert result.command_id == "cmd1"

    @pytest.mark.asyncio
    async def test_not_found(self, store):
        result = await store.get_active_transport_by_transport_id("t-missing")
        assert result is None


# ── get_active_commands_for_publishing ────────────────────────────────

class TestActiveCommandsForPublishing:
    @pytest.mark.asyncio
    async def test_returns_current(self, store):
        await store.register_command("cmd1", "t1", 0)
        result = await store.get_active_commands_for_publishing()
        assert "cmd1" in result

    @pytest.mark.asyncio
    async def test_empty_when_no_commands(self, store):
        result = await store.get_active_commands_for_publishing()
        assert result == {}
