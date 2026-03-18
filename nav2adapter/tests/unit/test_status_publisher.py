"""Tests for StatusPublisher orchestrator and extracted components.

Tests for:
- pose_parser.parse_position_status (was StatusPublisher._parse_pose_data)
- transport_watcher.get_progress_for_command (was StatusPublisher._calculate_progress / _get_progress_for_command)
- StatusPublisher start/stop lifecycle
"""
import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.pose_parser import parse_position_status
from services.transport_watcher import get_progress_for_command
from services.status_publisher import StatusPublisher
from domain.models import NavigationStatus, NavigationStatusEnum, PositionStatus, NavigationSession, Pose2D
from services.event_bus import EventBus


# -- parse_position_status (was _parse_pose_data) --------------------------

class TestParsePoseData:
    def test_standard_pose_dict(self):
        raw = {"pose": {"x": 1.0, "y": 2.0, "theta": 0.5}}
        result = parse_position_status(raw)
        assert result is not None
        assert result.x == 1.0
        assert result.y == 2.0
        assert result.theta == 0.5

    def test_direct_x_y_theta(self):
        raw = {"x": 3.0, "y": 4.0, "theta": 1.0}
        result = parse_position_status(raw)
        assert result is not None
        assert result.x == 3.0
        assert result.y == 4.0

    def test_wrapped_in_result(self):
        raw = {"result": {"pose": {"x": 5.0, "y": 6.0, "theta": 0.0}}}
        result = parse_position_status(raw)
        assert result is not None
        assert result.x == 5.0

    def test_normalized_x_m_y_m(self):
        raw = {"pose": {"x_m": 7.0, "y_m": 8.0, "theta_deg": 90.0}}
        result = parse_position_status(raw)
        assert result is not None
        assert result.x == 7.0
        assert result.y == 8.0
        assert abs(result.theta - math.radians(90.0)) < 1e-3

    def test_list_input(self):
        raw = [{"x": 1.0, "y": 2.0, "theta": 0.0}]
        result = parse_position_status(raw)
        assert result is not None

    def test_empty_list(self):
        result = parse_position_status([])
        assert result is None

    def test_non_dict(self):
        result = parse_position_status("invalid")
        assert result is None

    def test_missing_theta(self):
        raw = {"pose": {"x": 1.0, "y": 2.0}}
        result = parse_position_status(raw)
        assert result is None

    def test_falsy_zero_coordinates(self):
        """Zero coordinates must be preserved, not treated as missing."""
        raw = {"x": 0.0, "y": 0.0, "theta": 0.0}
        result = parse_position_status(raw)
        assert result is not None
        assert result.x == 0.0
        assert result.y == 0.0
        assert result.theta == 0.0


# -- get_progress_for_command (was _calculate_progress) --------------------

class TestCalculateProgress:
    @pytest.mark.asyncio
    async def test_finished(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        assert await get_progress_for_command(ss, "cmd1", fallback_state=8) == 100

    @pytest.mark.asyncio
    async def test_error(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        assert await get_progress_for_command(ss, "cmd1", fallback_state=7) == 0

    @pytest.mark.asyncio
    async def test_canceled(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        assert await get_progress_for_command(ss, "cmd1", fallback_state=6) == 0

    @pytest.mark.asyncio
    async def test_running(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        assert await get_progress_for_command(ss, "cmd1", fallback_state=5) == 50

    @pytest.mark.asyncio
    async def test_starting(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        assert await get_progress_for_command(ss, "cmd1", fallback_state=4) == 10

    @pytest.mark.asyncio
    async def test_pre_run(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        for s in (0, 1, 2, 3):
            assert await get_progress_for_command(ss, "cmd1", fallback_state=s) == 5


# -- start / stop lifecycle -----------------------------------------------

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self):
        ss = MagicMock()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
        ss.set_last_navigation_status = AsyncMock()
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            bus=EventBus(),
            state_store=ss,
        )
        await pub.start()
        assert pub._running_flag.is_set()
        assert len(pub._tasks) == 3
        await pub.stop()
        assert not pub._running_flag.is_set()

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        ss = MagicMock()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
        ss.set_last_navigation_status = AsyncMock()
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            bus=EventBus(),
            state_store=ss,
        )
        await pub.start()
        tasks_count = len(pub._tasks)
        await pub.start()  # second call should be no-op
        assert len(pub._tasks) == tasks_count
        await pub.stop()


# -- get_progress_for_command (was _get_progress_for_command) ---------------

class TestGetProgressForCommand:
    @pytest.mark.asyncio
    async def test_no_session_uses_state_fallback(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        progress = await get_progress_for_command(ss, "cmd1", fallback_state=5)
        assert progress == 50  # RUNNING

    @pytest.mark.asyncio
    async def test_terminal_state_returns_session_progress(self):
        session = MagicMock()
        session.progress_percent = 85
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=session)
        progress = await get_progress_for_command(ss, "cmd1", fallback_state=8)  # FINISHED
        assert progress == 85


# -- session terminal updates (now handled in transport_watcher) -----------

class TestSetSessionTerminal:
    @pytest.mark.asyncio
    async def test_updates_session(self):
        """execute_force_arrival updates session to 100% and clears transport."""
        from services.transport_watcher import execute_force_arrival
        session = MagicMock()
        session.progress_percent = 50
        session.min_remaining_dist_m = 5.0
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        ss.set_last_navigation_status = AsyncMock()
        ss.clear_transport = AsyncMock()
        bus = AsyncMock()
        bus.publish = AsyncMock()
        symovo = AsyncMock()
        await execute_force_arrival("cmd1", ss, bus, symovo)
        assert session.progress_percent == 100
        assert session.min_remaining_dist_m == 0.0
        ss.upsert_session.assert_awaited_once()
        ss.clear_transport.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_session_noop(self):
        """execute_force_arrival with no session still publishes success."""
        from services.transport_watcher import execute_force_arrival
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        ss.set_last_navigation_status = AsyncMock()
        ss.clear_transport = AsyncMock()
        bus = AsyncMock()
        bus.publish = AsyncMock()
        symovo = AsyncMock()
        await execute_force_arrival("cmd1", ss, bus, symovo)
        bus.publish.assert_awaited_once()
        ss.clear_transport.assert_awaited_once()
