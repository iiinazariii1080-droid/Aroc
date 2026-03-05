"""Tests for StatusPublisher — _parse_pose_data, _calculate_progress, start/stop, force-arrival."""
import pytest
import math
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from services.status_publisher import StatusPublisher
from domain.models import NavigationStatus, NavigationStatusEnum, PositionStatus, NavigationSession, Pose2D
from services.event_bus import EventBus


# ── _parse_pose_data (static method) ─────────────────────────────────

class TestParsePoseData:
    def test_standard_pose_dict(self):
        raw = {"pose": {"x": 1.0, "y": 2.0, "theta": 0.5}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert result.x == 1.0
        assert result.y == 2.0
        assert result.theta == 0.5

    def test_direct_x_y_theta(self):
        raw = {"x": 3.0, "y": 4.0, "theta": 1.0}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert result.x == 3.0
        assert result.y == 4.0

    def test_wrapped_in_result(self):
        raw = {"result": {"pose": {"x": 5.0, "y": 6.0, "theta": 0.0}}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert result.x == 5.0

    def test_normalized_x_m_y_m(self):
        raw = {"pose": {"x_m": 7.0, "y_m": 8.0, "theta_deg": 90.0}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert result.x == 7.0
        assert result.y == 8.0
        assert abs(result.theta - math.radians(90.0)) < 1e-3

    def test_list_input(self):
        raw = [{"x": 1.0, "y": 2.0, "theta": 0.0}]
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None

    def test_empty_list(self):
        result = StatusPublisher._parse_pose_data([])
        assert result is None

    def test_non_dict(self):
        result = StatusPublisher._parse_pose_data("invalid")
        assert result is None

    def test_missing_theta(self):
        raw = {"pose": {"x": 1.0, "y": 2.0}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is None

    def test_falsy_zero_coordinates(self):
        """Zero coordinates must be preserved, not treated as missing."""
        raw = {"x": 0.0, "y": 0.0, "theta": 0.0}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert result.x == 0.0
        assert result.y == 0.0
        assert result.theta == 0.0


# ── _calculate_progress ──────────────────────────────────────────────

class TestCalculateProgress:
    def setup_method(self):
        self.pub = StatusPublisher.__new__(StatusPublisher)

    def test_finished(self):
        assert self.pub._calculate_progress(8, {}) == 100

    def test_error(self):
        assert self.pub._calculate_progress(7, {}) == 0

    def test_canceled(self):
        assert self.pub._calculate_progress(10, {}) == 0

    def test_running(self):
        assert self.pub._calculate_progress(5, {}) == 50

    def test_starting(self):
        assert self.pub._calculate_progress(4, {}) == 10

    def test_pre_run(self):
        # UNASSIGNED=0, ASSIGNED=1, RECEIVED=2, UNKNOWN=3
        for s in (0, 1, 2, 3):
            assert self.pub._calculate_progress(s, {}) == 5


# ── start / stop lifecycle ───────────────────────────────────────────

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self):
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            mqtt_adapter=None,
            bus=EventBus(),
        )
        with patch.object(pub, "_transport_manager", new_callable=AsyncMock) as tm, \
             patch.object(pub, "_watch_position_longpoll", new_callable=AsyncMock) as wp, \
             patch.object(pub, "_watch_status_loop", new_callable=AsyncMock) as ws:
            await pub.start()
            assert pub._running is True
            assert len(pub._tasks) == 3
            await pub.stop()
            assert pub._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            mqtt_adapter=None,
            bus=EventBus(),
        )
        with patch.object(pub, "_transport_manager", new_callable=AsyncMock), \
             patch.object(pub, "_watch_position_longpoll", new_callable=AsyncMock), \
             patch.object(pub, "_watch_status_loop", new_callable=AsyncMock):
            await pub.start()
            tasks_count = len(pub._tasks)
            await pub.start()  # second call should be no-op
            assert len(pub._tasks) == tasks_count
            await pub.stop()


# ── _get_progress_for_command ─────────────────────────────────────────

class TestGetProgressForCommand:
    @pytest.mark.asyncio
    async def test_no_session_uses_state_fallback(self):
        pub = StatusPublisher.__new__(StatusPublisher)
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=None)
            progress = await pub._get_progress_for_command("cmd1", fallback_state=5)
        assert progress == 50  # RUNNING

    @pytest.mark.asyncio
    async def test_terminal_state_returns_session_progress(self):
        pub = StatusPublisher.__new__(StatusPublisher)
        session = MagicMock()
        session.progress_percent = 85
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=session)
            progress = await pub._get_progress_for_command("cmd1", fallback_state=8)  # FINISHED
        assert progress == 85


# ── _set_session_terminal ─────────────────────────────────────────────

class TestSetSessionTerminal:
    @pytest.mark.asyncio
    async def test_updates_session(self):
        pub = StatusPublisher.__new__(StatusPublisher)
        session = MagicMock()
        session.progress_percent = 50
        session.min_remaining_dist_m = 5.0
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=session)
            ss.upsert_session = AsyncMock()
            await pub._set_session_terminal("cmd1", progress_percent=100)
            assert session.progress_percent == 100
            assert session.min_remaining_dist_m == 0.0
            ss.upsert_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_session_noop(self):
        pub = StatusPublisher.__new__(StatusPublisher)
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=None)
            ss.upsert_session = AsyncMock()
            await pub._set_session_terminal("cmd1", progress_percent=100)
            ss.upsert_session.assert_not_awaited()
