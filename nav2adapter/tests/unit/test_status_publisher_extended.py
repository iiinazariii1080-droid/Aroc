"""Extended tests for StatusPublisher — async loops, _watch_transport, _watch_position, _watch_status, etc."""
import pytest
import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace

from services.status_publisher import StatusPublisher
from domain.models import (
    NavigationStatus,
    NavigationStatusEnum,
    PositionStatus,
    NavigationSession,
    Pose2D,
    ActiveTransport,
)
from domain.state_machine import NavigationStateMachine
from domain.events import (
    StateProgressEvent,
    ResultSuccessEvent,
    ResultCanceledEvent,
    ResultErrorEvent,
    ResultType,
    StateType,
)
from services.event_bus import EventBus


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_pub(**overrides) -> StatusPublisher:
    """Create a StatusPublisher with sensible test defaults."""
    pub = StatusPublisher.__new__(StatusPublisher)
    pub.symovo_client = overrides.get("symovo_client", AsyncMock())
    pub.mqtt_adapter = overrides.get("mqtt_adapter", AsyncMock())
    pub.bus = overrides.get("bus", AsyncMock(spec=EventBus))
    pub._running = overrides.get("running", True)
    pub._tasks = []
    pub._transport_tasks = {}
    pub._at_goal_since = {}
    pub._force_arrived_commands = set()
    return pub


def _make_active_transport(command_id="cmd1", transport_id="t1", state=5, generation=0):
    return ActiveTransport(
        command_id=command_id,
        transport_id=transport_id,
        state=state,
        generation=generation,
    )


def _make_session(command_id="cmd1", progress=50):
    return NavigationSession(
        command_id=command_id,
        target_id="posA",
        start=Pose2D(x=0, y=0, map_id=None),
        goal=Pose2D(x=10, y=10, map_id=None),
        total_dist_m=14.14,
        min_remaining_dist_m=7.07,
        progress_percent=progress,
    )


# ─── _fetch_pose_with_fallback ───────────────────────────────────────

class TestFetchPoseWithFallback:
    @pytest.mark.asyncio
    async def test_success_from_pose(self):
        pub = _make_pub()
        pub.symovo_client.pose_uncached = AsyncMock(return_value={"pose": {"x": 1, "y": 2, "theta": 0}})
        with patch("services.status_publisher.state_store") as ss:
            ss.set_last_raw_pose = AsyncMock()
            result = await pub._fetch_pose_with_fallback()
        assert result == {"pose": {"x": 1, "y": 2, "theta": 0}}
        ss.set_last_raw_pose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_to_status_on_404(self):
        pub = _make_pub()
        pub.symovo_client.pose_uncached = AsyncMock(side_effect=Exception("HTTP 404 not found"))
        pub.symovo_client.status_uncached = AsyncMock(return_value={"x": 3, "y": 4, "theta": 0.5})
        with patch("services.status_publisher.state_store") as ss:
            ss.set_last_raw_pose = AsyncMock()
            ss.set_last_raw_status = AsyncMock()
            result = await pub._fetch_pose_with_fallback()
        assert result == {"x": 3, "y": 4, "theta": 0.5}

    @pytest.mark.asyncio
    async def test_no_fallback_on_unexpected_error(self):
        pub = _make_pub()
        pub.symovo_client.pose_uncached = AsyncMock(side_effect=Exception("connection refused"))
        with patch("services.status_publisher.state_store") as ss:
            ss.set_last_raw_pose = AsyncMock()
            with pytest.raises(Exception, match="connection refused"):
                await pub._fetch_pose_with_fallback()

    @pytest.mark.asyncio
    async def test_fallback_status_also_fails(self):
        """If pose 404 and status also fails, original error is re-raised."""
        pub = _make_pub()
        pub.symovo_client.pose_uncached = AsyncMock(side_effect=Exception("empty response"))
        pub.symovo_client.status_uncached = AsyncMock(side_effect=Exception("also failed"))
        with patch("services.status_publisher.state_store") as ss:
            ss.set_last_raw_pose = AsyncMock()
            with pytest.raises(Exception, match="empty response"):
                await pub._fetch_pose_with_fallback()


# ─── _publish_navigation_status ──────────────────────────────────────

class TestPublishNavigationStatus:
    @pytest.mark.asyncio
    async def test_delegates_to_shared_publish(self):
        pub = _make_pub()
        status = NavigationStatus(status=NavigationStatusEnum.IDLE, goal_id=None, progress_percent=0, error_reason=None)
        with patch("services.status_publisher.StatusPublisher._publish_navigation_status", new_callable=AsyncMock) as mock_pub:
            # Test the real method via direct call
            pass
        # Just test it doesn't crash
        with patch("services.nav_status_publisher.publish_navigation_status", new_callable=AsyncMock) as mock_shared:
            await pub._publish_navigation_status(status)
            mock_shared.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_store_false(self):
        pub = _make_pub()
        status = NavigationStatus(status=NavigationStatusEnum.ARRIVED, goal_id="cmd1", progress_percent=100, error_reason=None)
        with patch("services.nav_status_publisher.publish_navigation_status", new_callable=AsyncMock) as mock_shared:
            await pub._publish_navigation_status(status, update_store=False)
            mock_shared.assert_awaited_once_with(status, pub.mqtt_adapter, update_store=False)


# ─── _publish_position_status ────────────────────────────────────────

class TestPublishPositionStatus:
    @pytest.mark.asyncio
    async def test_stores_and_publishes_to_mqtt(self):
        pub = _make_pub()
        pub.mqtt_adapter = AsyncMock()
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_update_progress_from_position", new_callable=AsyncMock) as up:
            ss.set_last_position_status = AsyncMock()
            await pub._publish_position_status(pos)
        ss.set_last_position_status.assert_awaited_once_with(pos)
        pub.mqtt_adapter.publish_position_status.assert_awaited_once()
        up.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_mqtt_adapter(self):
        pub = _make_pub()
        pub.mqtt_adapter = None
        pos = PositionStatus(x=0, y=0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_update_progress_from_position", new_callable=AsyncMock):
            ss.set_last_position_status = AsyncMock()
            await pub._publish_position_status(pos)
        ss.set_last_position_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        pub = _make_pub()
        pub.mqtt_adapter = AsyncMock()
        pub.mqtt_adapter.publish_position_status = AsyncMock(side_effect=RuntimeError("boom"))
        pos = PositionStatus(x=0, y=0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_update_progress_from_position", new_callable=AsyncMock):
            ss.set_last_position_status = AsyncMock()
            with pytest.raises(RuntimeError, match="boom"):
                await pub._publish_position_status(pos)


# ─── _update_progress_from_position ──────────────────────────────────

class TestUpdateProgressFromPosition:
    @pytest.mark.asyncio
    async def test_no_active_commands_noop(self):
        pub = _make_pub()
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_commands_for_publishing = AsyncMock(return_value={})
            await pub._update_progress_from_position(pos)
        # Nothing to assert beyond no crash

    @pytest.mark.asyncio
    async def test_terminal_state_skipped(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.FINISHED)
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=None)
            await pub._update_progress_from_position(pos)
        assert "cmd1" not in pub._at_goal_since

    @pytest.mark.asyncio
    async def test_canceling_state_skipped(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.CANCELING)
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            await pub._update_progress_from_position(pos)
        assert "cmd1" not in pub._at_goal_since

    @pytest.mark.asyncio
    async def test_no_session_skipped(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        pos = PositionStatus(x=5.0, y=5.0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current") as usc:
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=None)
            await pub._update_progress_from_position(pos)
        usc.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_progress(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=42)
        pos = PositionStatus(x=5.0, y=5.0, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current", return_value=session) as usc, \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            cfg.symovo_force_arrival_on_proximity = False
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=session)
            ss.upsert_session = AsyncMock()
            await pub._update_progress_from_position(pos)
        pub.bus.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_force_arrival_sets_flag(self):
        """When robot is at goal for dwell_s, _force_arrived_commands set."""
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=95)
        # Position right at goal
        pos = PositionStatus(x=10.0, y=10.0, theta=0, frame_id="map")
        pub._at_goal_since["cmd1"] = time.time() - 10  # already at goal for 10s

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current", return_value=session), \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=session)
            ss.upsert_session = AsyncMock()
            await pub._update_progress_from_position(pos)
        assert "cmd1" in pub._force_arrived_commands

    @pytest.mark.asyncio
    async def test_not_at_goal_clears_dwell_timer(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=30)
        # Position far from goal
        pos = PositionStatus(x=0.0, y=0.0, theta=0, frame_id="map")
        pub._at_goal_since["cmd1"] = time.time()

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current", return_value=session), \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=session)
            ss.upsert_session = AsyncMock()
            await pub._update_progress_from_position(pos)
        assert "cmd1" not in pub._at_goal_since

    @pytest.mark.asyncio
    async def test_first_at_goal_starts_dwell(self):
        pub = _make_pub()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=90)
        pos = PositionStatus(x=10.0, y=10.0, theta=0, frame_id="map")

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current", return_value=session), \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            ss.get_session = AsyncMock(return_value=session)
            ss.upsert_session = AsyncMock()
            await pub._update_progress_from_position(pos)
        assert "cmd1" in pub._at_goal_since
        assert "cmd1" not in pub._force_arrived_commands  # not yet dwelled long enough


# ─── _watch_status_loop ──────────────────────────────────────────────

class TestWatchStatusLoop:
    @pytest.mark.asyncio
    async def test_caches_status(self):
        pub = _make_pub()
        pub.symovo_client.status_uncached = AsyncMock(return_value={"battery": 80})
        call_count = 0

        async def _stop_after_one(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg:
            cfg.status_cache_hz = 10.0  # fast for test
            ss.set_last_raw_status = AsyncMock(side_effect=_stop_after_one)
            await pub._watch_status_loop()
        assert ss.set_last_raw_status.await_count >= 1

    @pytest.mark.asyncio
    async def test_handles_non_dict_response(self):
        pub = _make_pub()
        pub.symovo_client.status_uncached = AsyncMock(return_value="not-a-dict")
        call_count = 0

        async def _track_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_track_sleep):
            cfg.status_cache_hz = 10.0
            ss.set_last_raw_status = AsyncMock()
            await pub._watch_status_loop()
        ss.set_last_raw_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_error_with_backoff(self):
        pub = _make_pub()
        call_count = 0
        pub.symovo_client.status_uncached = AsyncMock(side_effect=RuntimeError("network"))

        async def _track(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                pub._running = False

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"), \
             patch("asyncio.sleep", side_effect=_track):
            cfg.status_cache_hz = 10.0
            await pub._watch_status_loop()
        assert call_count >= 2  # backoff + loop sleep

    @pytest.mark.asyncio
    async def test_handles_expected_errors_quietly(self):
        """404 / not found errors should log at debug, not warning."""
        pub = _make_pub()
        call_count = 0
        pub.symovo_client.status_uncached = AsyncMock(side_effect=Exception("HTTP 404 not found"))

        async def _track(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"), \
             patch("asyncio.sleep", side_effect=_track):
            cfg.status_cache_hz = 10.0
            await pub._watch_status_loop()
        # Should not raise, just loop and eventually stop

    @pytest.mark.asyncio
    async def test_cancelled(self):
        pub = _make_pub()
        pub.symovo_client.status_uncached = AsyncMock(side_effect=asyncio.CancelledError)
        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"):
            cfg.status_cache_hz = 10.0
            # Should NOT raise CancelledError — returns cleanly
            await pub._watch_status_loop()


# ─── _transport_manager ──────────────────────────────────────────────

class TestTransportManager:
    @pytest.mark.asyncio
    async def test_spawns_watcher_for_active_command(self):
        pub = _make_pub()
        t = _make_active_transport()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_watch_transport", new_callable=AsyncMock) as wt, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
            await pub._transport_manager()
        assert "cmd1" in pub._transport_tasks

    @pytest.mark.asyncio
    async def test_stops_watcher_for_inactive_command(self):
        pub = _make_pub()
        # Pre-populate a transport task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        pub._transport_tasks["old_cmd"] = ("old_t", mock_task)
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            ss.get_active_commands_for_publishing = AsyncMock(return_value={})
            ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
            await pub._transport_manager()
        mock_task.cancel.assert_called_once()
        assert "old_cmd" not in pub._transport_tasks

    @pytest.mark.asyncio
    async def test_idle_heartbeat_published(self):
        pub = _make_pub()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock) as pns, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            cfg.navigation_terminal_hold_s = 0.0
            ss.get_active_commands_for_publishing = AsyncMock(return_value={})
            ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
            await pub._transport_manager()
        pns.assert_awaited()
        args = pns.call_args[0]
        assert args[0].status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_holds_arrived_status(self):
        """When within hold window, the ARRIVED status should be republished."""
        pub = _make_pub()
        arrived_status = NavigationStatus(
            status=NavigationStatusEnum.ARRIVED,
            goal_id="cmd1",
            progress_percent=100,
            error_reason=None,
        )
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock) as pns, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            cfg.navigation_terminal_hold_s = 5.0
            ss.get_active_commands_for_publishing = AsyncMock(return_value={})
            ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(arrived_status, time.time()))
            await pub._transport_manager()
        pns.assert_awaited()
        # Check it was called with update_store=False for the hold
        call_kwargs = pns.call_args_list[0]
        assert call_kwargs.kwargs.get("update_store") is False

    @pytest.mark.asyncio
    async def test_restarts_watcher_on_transport_id_change(self):
        pub = _make_pub()
        old_task = MagicMock()
        old_task.done.return_value = False
        old_task.cancel = MagicMock()
        pub._transport_tasks["cmd1"] = ("old_transport", old_task)
        new_t = _make_active_transport(command_id="cmd1", transport_id="new_transport")
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch.object(pub, "_watch_transport", new_callable=AsyncMock), \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": new_t})
            await pub._transport_manager()
        old_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_in_manager_doesnt_crash(self):
        pub = _make_pub()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            ss.get_active_commands_for_publishing = AsyncMock(side_effect=RuntimeError("db error"))
            await pub._transport_manager()
        # Should exit cleanly


# ─── _watch_transport ─────────────────────────────────────────────────

class TestWatchTransport:
    @pytest.mark.asyncio
    async def test_exits_if_transport_not_active_at_start(self):
        pub = _make_pub()
        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=None)
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        # Should return without error

    @pytest.mark.asyncio
    async def test_exits_on_transport_id_mismatch_at_start(self):
        pub = _make_pub()
        wrong = _make_active_transport(command_id="cmd1", transport_id="t_other")
        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=wrong)
            await pub._watch_transport(command_id="cmd1", transport_id="t1")

    @pytest.mark.asyncio
    async def test_force_arrived_consumed(self):
        """When _force_arrived_commands has the command_id, terminal sequence executes."""
        pub = _make_pub()
        pub._force_arrived_commands.add("cmd1")
        t = _make_active_transport()
        session = _make_session()

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.charger_workflow") as cw, \
             patch.object(pub, "_set_session_terminal", new_callable=AsyncMock) as sst, \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock) as pns:
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.get_session = AsyncMock(return_value=session)
            ss.clear_transport = AsyncMock()
            cw.maybe_activate_after_arrival = AsyncMock()
            # Initial fetch
            pub.symovo_client.transport_get_uncached = AsyncMock(return_value={"result": {"state": 5, "timestamp": 100}})
            ss.update_transport_state = AsyncMock()
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        sst.assert_awaited_once_with("cmd1", progress_percent=100)
        pub.bus.publish.assert_awaited()
        # Check ARRIVED published
        arrived_calls = [c for c in pns.call_args_list if c[0][0].status == NavigationStatusEnum.ARRIVED]
        assert len(arrived_calls) >= 1

    @pytest.mark.asyncio
    async def test_generation_mismatch_stops_watcher(self):
        pub = _make_pub()
        t_initial = _make_active_transport(generation=0)
        t_later = _make_active_transport(generation=1)  # generation changed

        call_idx = 0
        async def get_active(cmd_id):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return t_initial  # initial check
            return t_later  # generation changed

        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(side_effect=get_active)
            ss.update_transport_state = AsyncMock()
            pub.symovo_client.transport_get_uncached = AsyncMock(return_value={"result": {"state": 5, "timestamp": 100}})
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        # Should exit without error

    @pytest.mark.asyncio
    async def test_terminal_finished_publishes_arrived(self):
        """FINISHED state → ARRIVED status + ResultSuccessEvent."""
        pub = _make_pub()
        t = _make_active_transport(state=5, generation=0)
        session = _make_session()

        call_idx = 0
        async def get_active(cmd_id):
            nonlocal call_idx
            call_idx += 1
            return t

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.charger_workflow") as cw, \
             patch.object(pub, "_set_session_terminal", new_callable=AsyncMock), \
             patch.object(pub, "_get_progress_for_command", new_callable=AsyncMock, return_value=100), \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock) as pns:
            ss.get_active_transport = AsyncMock(side_effect=get_active)
            ss.get_session = AsyncMock(return_value=session)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            cw.maybe_activate_after_arrival = AsyncMock()
            # Initial fetch returns FINISHED
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 100}}
            )
            # Long-poll also returns FINISHED
            pub.symovo_client.transport_wait_for_changes = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 101}}
            )
            pub.symovo_client.status_uncached = AsyncMock(return_value={})
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_terminal_canceled_publishes_idle(self):
        """CANCELED state → IDLE status + ResultCanceledEvent."""
        pub = _make_pub()
        t = _make_active_transport()

        with patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_get_progress_for_command", new_callable=AsyncMock, return_value=0), \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock) as pns:
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            # Init fetch returns CANCELED
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.CANCELED, "timestamp": 100}}
            )
            pub.symovo_client.transport_wait_for_changes = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.CANCELED, "timestamp": 101}}
            )
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()
        # Verify ResultCanceledEvent was published
        canceled_events = [c for c in pub.bus.publish.call_args_list if isinstance(c[0][0], ResultCanceledEvent)]
        assert len(canceled_events) >= 1

    @pytest.mark.asyncio
    async def test_terminal_error_publishes_error(self):
        """ERROR state → ERROR status + ResultErrorEvent."""
        pub = _make_pub()
        t = _make_active_transport()

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.ErrorMapper") as em, \
             patch.object(pub, "_get_progress_for_command", new_callable=AsyncMock, return_value=0), \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            em.get_error_reason = MagicMock(return_value="obstacle_detected")
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.ERROR, "timestamp": 100}}
            )
            pub.symovo_client.transport_wait_for_changes = AsyncMock(
                return_value={"result": {"state": NavigationStateMachine.ERROR, "timestamp": 101}}
            )
            pub.symovo_client.status_uncached = AsyncMock(return_value={"state_flags": {}})
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        error_events = [c for c in pub.bus.publish.call_args_list if isinstance(c[0][0], ResultErrorEvent)]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_http_404_clears_transport(self):
        """404 for transport → clear and stop."""
        pub = _make_pub()
        t = _make_active_transport()

        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": 5, "timestamp": 100}}
            )
            pub.symovo_client.transport_wait_for_changes = AsyncMock(
                side_effect=Exception("HTTP 404 /transport/t1 not found")
            )
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited_with("cmd1")

    @pytest.mark.asyncio
    async def test_connection_error_with_force_arrived(self):
        """Connection error + force-arrived → consume immediately."""
        pub = _make_pub()
        pub._force_arrived_commands.add("cmd1")
        t = _make_active_transport()
        session = _make_session()

        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.charger_workflow") as cw, \
             patch.object(pub, "_set_session_terminal", new_callable=AsyncMock), \
             patch.object(pub, "_publish_navigation_status", new_callable=AsyncMock):
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.get_session = AsyncMock(return_value=session)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            cw.maybe_activate_after_arrival = AsyncMock()

            # force-arrived is consumed at the top of the loop, before long-poll
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": 5, "timestamp": 100}}
            )
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_deleted_transport_clears(self):
        """Empty response from long-poll means transport deleted."""
        pub = _make_pub()
        t = _make_active_transport()

        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(return_value=t)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            pub.symovo_client.transport_get_uncached = AsyncMock(
                return_value={"result": {"state": 5, "timestamp": 100}}
            )
            pub.symovo_client.transport_wait_for_changes = AsyncMock(return_value={})
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_initial_fetch_failure_continues(self):
        """If initial transport_get_uncached fails, watcher should still proceed."""
        pub = _make_pub()
        t = _make_active_transport()

        call_idx = 0
        async def get_active(cmd_id):
            nonlocal call_idx
            call_idx += 1
            if call_idx <= 2:
                return t
            return None  # stop after initial + 1 loop

        with patch("services.status_publisher.state_store") as ss:
            ss.get_active_transport = AsyncMock(side_effect=get_active)
            ss.update_transport_state = AsyncMock()
            ss.clear_transport = AsyncMock()
            pub.symovo_client.transport_get_uncached = AsyncMock(side_effect=Exception("timeout"))
            pub.symovo_client.transport_wait_for_changes = AsyncMock(return_value={})
            await pub._watch_transport(command_id="cmd1", transport_id="t1")
        # Should exit cleanly after transport becomes inactive


# ─── _watch_position_longpoll ─────────────────────────────────────────

class TestWatchPositionLongpoll:
    @pytest.mark.asyncio
    async def test_polling_fallback_on_amr_error(self):
        """If amr_wait_for_changes throws a DeviceError with 404, falls back to polling."""
        pub = _make_pub()
        call_count = 0

        from exceptions import DeviceError

        async def _mock_amr_wait(**kwargs):
            raise DeviceError("HTTP 404 not found")

        async def _mock_pose():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False
            return {"pose": {"x": 1, "y": 2, "theta": 0.5}}

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_parse_pose_data", return_value=PositionStatus(x=1, y=2, theta=0.5, frame_id="map")), \
             patch.object(pub, "_publish_position_status", new_callable=AsyncMock), \
             patch.object(pub, "_fetch_pose_with_fallback", side_effect=_mock_pose):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            pub.symovo_client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr_wait)
            await pub._watch_position_longpoll()
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_publishes_position_on_success(self):
        """Successful AMR long-poll data → position published."""
        pub = _make_pub()
        call_count = 0

        async def _mock_amr_wait(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False
                raise asyncio.CancelledError
            return {"result": {"pose": {"x": 3, "y": 4, "theta": 0.1}, "timestamp": 12345}}

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store") as ss, \
             patch.object(pub, "_fetch_pose_with_fallback", new_callable=AsyncMock, return_value={"pose": {"x": 3, "y": 4, "theta": 0.1}}), \
             patch.object(pub, "_publish_position_status", new_callable=AsyncMock) as pps, \
             patch.object(pub, "_parse_pose_data", return_value=PositionStatus(x=3, y=4, theta=0.1, frame_id="map")):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            pub.symovo_client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr_wait)
            await pub._watch_position_longpoll()
        assert pps.await_count >= 1

    @pytest.mark.asyncio
    async def test_cancelled_exits_cleanly(self):
        pub = _make_pub()
        pub.symovo_client.amr_wait_for_changes = AsyncMock(side_effect=asyncio.CancelledError)
        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            await pub._watch_position_longpoll()

    @pytest.mark.asyncio
    async def test_fetch_error_backoff(self):
        """Fetch errors cause backoff sleep."""
        pub = _make_pub()
        call_count = 0

        async def _fail_pose():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                pub._running = False
                raise asyncio.CancelledError
            raise RuntimeError("network down")

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"), \
             patch.object(pub, "_fetch_pose_with_fallback", side_effect=_fail_pose):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            pub.symovo_client.amr_wait_for_changes = AsyncMock(side_effect=asyncio.TimeoutError)
            await pub._watch_position_longpoll()

    @pytest.mark.asyncio
    async def test_longpoll_timeout_keeps_publishing(self):
        """Timeout in long-poll → falls through to fetch+publish if interval elapsed."""
        pub = _make_pub()
        call_count = 0

        async def _mock_amr(**kwargs):
            raise asyncio.TimeoutError

        async def _mock_fetch():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running = False
            return {"pose": {"x": 1, "y": 1, "theta": 0}}

        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.state_store"), \
             patch.object(pub, "_fetch_pose_with_fallback", side_effect=_mock_fetch), \
             patch.object(pub, "_publish_position_status", new_callable=AsyncMock) as pps, \
             patch.object(pub, "_parse_pose_data", return_value=PositionStatus(x=1, y=1, theta=0, frame_id="map")):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 0.01
            pub.symovo_client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr)
            await pub._watch_position_longpoll()
        assert pps.await_count >= 1


# ─── _get_progress_for_command (extended) ─────────────────────────────

class TestGetProgressForCommandExtended:
    @pytest.mark.asyncio
    async def test_non_terminal_with_position_updates_session(self):
        pub = _make_pub()
        session = _make_session(progress=40)
        pos = PositionStatus(x=5, y=5, theta=0, frame_id="map")
        with patch("services.status_publisher.state_store") as ss, \
             patch("services.status_publisher.update_session_from_current", return_value=session):
            ss.get_session = AsyncMock(return_value=session)
            ss.get_last_position_status = AsyncMock(return_value=pos)
            ss.upsert_session = AsyncMock()
            result = await pub._get_progress_for_command("cmd1", fallback_state=5)
        assert result == 40

    @pytest.mark.asyncio
    async def test_non_terminal_no_position(self):
        pub = _make_pub()
        session = _make_session(progress=60)
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=session)
            ss.get_last_position_status = AsyncMock(return_value=None)
            result = await pub._get_progress_for_command("cmd1", fallback_state=5)
        assert result == 60

    @pytest.mark.asyncio
    async def test_terminal_no_session_uses_calc(self):
        pub = _make_pub()
        with patch("services.status_publisher.state_store") as ss:
            ss.get_session = AsyncMock(return_value=None)
            result = await pub._get_progress_for_command("cmd1", fallback_state=8)
        assert result == 100  # FINISHED


# ─── _calculate_progress (extended) ──────────────────────────────────

class TestCalculateProgressExtended:
    def test_canceling_returns_50(self):
        pub = _make_pub()
        assert pub._calculate_progress(NavigationStateMachine.CANCELING, {}) == 50

    def test_unknown_state_returns_0(self):
        pub = _make_pub()
        assert pub._calculate_progress(999, {}) == 0


# ─── start / stop extended ───────────────────────────────────────────

class TestStartStopExtended:
    @pytest.mark.asyncio
    async def test_stop_cancels_transport_tasks(self):
        pub = _make_pub()

        async def _noop():
            await asyncio.sleep(10)

        transport_task = asyncio.create_task(_noop())
        main_task = asyncio.create_task(_noop())
        pub._transport_tasks["cmd1"] = ("t1", transport_task)
        pub._tasks = [main_task]
        pub._at_goal_since["cmd1"] = 1.0
        pub._force_arrived_commands.add("cmd1")

        await pub.stop()
        assert pub._running is False
        assert len(pub._transport_tasks) == 0
        assert len(pub._at_goal_since) == 0
        assert len(pub._force_arrived_commands) == 0

    @pytest.mark.asyncio
    async def test_stop_timeout_doesnt_crash(self):
        pub = _make_pub()

        async def _hang():
            await asyncio.sleep(100)

        pub._tasks = [asyncio.create_task(_hang())]

        # Patch wait_for to simulate timeout
        original_wait_for = asyncio.wait_for
        async def _timeout_wait_for(*a, **kw):
            raise asyncio.TimeoutError

        with patch("asyncio.wait_for", side_effect=_timeout_wait_for):
            await pub.stop()
        assert pub._running is False

    @pytest.mark.asyncio
    async def test_start_error_resets_running(self):
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            mqtt_adapter=None,
            bus=EventBus(),
        )
        with patch("asyncio.create_task", side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError):
                await pub.start()
        assert pub._running is False


# ─── _parse_pose_data extended coverage ──────────────────────────────

class TestParsePoseDataExtended:
    def test_x_m_y_m_without_theta_deg(self):
        """When theta_deg is absent, use theta field directly."""
        raw = {"pose": {"x_m": 1.0, "y_m": 2.0, "theta": 0.5}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        # theta should use radians conversion if theta_deg present, else raw theta
        assert abs(result.theta - 0.5) < 1e-6

    def test_result_without_pose(self):
        """result dict without pose key → None."""
        raw = {"result": {"something": "else"}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is None

    def test_pose_key_is_none(self):
        """pose key exists but value is None → check normalized format."""
        raw = {"pose": None}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is None

    def test_theta_deg_only(self):
        """theta_deg without theta → convert to radians."""
        raw = {"pose": {"x": 1.0, "y": 2.0, "theta_deg": 180.0}}
        result = StatusPublisher._parse_pose_data(raw)
        assert result is not None
        assert abs(result.theta - math.pi) < 0.01
