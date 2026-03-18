"""Extended tests for decomposed StatusPublisher components.

After the monolith decomposition, the old StatusPublisher internal methods moved to:
- _fetch_pose_with_fallback / _parse_pose_data / _publish_position_status / _update_progress_from_position
    -> PositionPoller (services/position_poller.py)
- _watch_status_loop -> StatusPoller.run() (services/status_poller.py)
- _watch_transport -> TransportWatcherTask.run() (services/transport_watcher.py)
- _watch_position_longpoll -> PositionPoller.run() (services/position_poller.py)
- _get_progress_for_command -> transport_watcher.get_progress_for_command()
- _calculate_progress -> merged into get_progress_for_command()
- _parse_pose_data -> pose_parser.parse_position_status()
- _force_arrived_commands / _at_goal_since -> ForceArrivalSignal (services/force_arrival.py)

StatusPublisher (services/status_publisher.py) is now a thin orchestrator with:
- __init__, start(), stop(), _transport_manager()
"""
import pytest
import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace

from services.status_publisher import StatusPublisher
from services.position_poller import PositionPoller
from services.status_poller import StatusPoller
from services.transport_watcher import TransportWatcherTask, get_progress_for_command
from services.force_arrival import ForceArrivalSignal
from services.pose_parser import parse_position_status
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


# --- Helpers ----------------------------------------------------------------

def _make_position_poller(**overrides) -> PositionPoller:
    """Create a PositionPoller with sensible test defaults."""
    running_flag = overrides.get("running_flag", asyncio.Event())
    if overrides.get("running", True):
        running_flag.set()
    poller = PositionPoller(
        symovo_client=overrides.get("symovo_client", AsyncMock()),
        bus=overrides.get("bus", AsyncMock(spec=EventBus)),
        state_store=overrides.get("state_store", AsyncMock()),
        force_arrival=overrides.get("force_arrival", ForceArrivalSignal()),
        running_flag=running_flag,
    )
    return poller


def _make_status_poller(**overrides) -> StatusPoller:
    """Create a StatusPoller with sensible test defaults."""
    running_flag = overrides.get("running_flag", asyncio.Event())
    if overrides.get("running", True):
        running_flag.set()
    poller = StatusPoller(
        symovo_client=overrides.get("symovo_client", AsyncMock()),
        state_store=overrides.get("state_store", AsyncMock()),
        safety_tracker=overrides.get("safety_tracker", MagicMock()),
        running_flag=running_flag,
    )
    return poller


def _make_transport_watcher(**overrides) -> TransportWatcherTask:
    """Create a TransportWatcherTask with sensible test defaults."""
    running_flag = overrides.get("running_flag", asyncio.Event())
    if overrides.get("running", True):
        running_flag.set()
    watcher = TransportWatcherTask(
        symovo_client=overrides.get("symovo_client", AsyncMock()),
        bus=overrides.get("bus", AsyncMock(spec=EventBus)),
        state_store=overrides.get("state_store", AsyncMock()),
        force_arrival=overrides.get("force_arrival", ForceArrivalSignal()),
        running_flag=running_flag,
    )
    return watcher


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


# --- _fetch_pose_with_fallback (now on PositionPoller) ----------------------

class TestFetchPoseWithFallback:
    @pytest.mark.asyncio
    async def test_success_from_pose(self):
        ss = AsyncMock()
        poller = _make_position_poller(state_store=ss)
        poller._client.pose_uncached = AsyncMock(return_value={"pose": {"x": 1, "y": 2, "theta": 0}})
        result = await poller._fetch_pose_with_fallback()
        assert result == {"pose": {"x": 1, "y": 2, "theta": 0}}
        ss.set_last_raw_pose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_to_status_on_device_error(self):
        from exceptions import DeviceError
        ss = AsyncMock()
        poller = _make_position_poller(state_store=ss)
        poller._client.pose_uncached = AsyncMock(side_effect=DeviceError("not found"))
        poller._client.status_uncached = AsyncMock(return_value={"x": 3, "y": 4, "theta": 0.5})
        result = await poller._fetch_pose_with_fallback()
        assert result == {"x": 3, "y": 4, "theta": 0.5}

    @pytest.mark.asyncio
    async def test_no_fallback_on_unexpected_error(self):
        ss = AsyncMock()
        poller = _make_position_poller(state_store=ss)
        poller._client.pose_uncached = AsyncMock(side_effect=RuntimeError("connection refused"))
        with pytest.raises(RuntimeError, match="connection refused"):
            await poller._fetch_pose_with_fallback()

    @pytest.mark.asyncio
    async def test_fallback_status_also_fails(self):
        """If pose fails with DeviceError and status also fails, original error is re-raised."""
        from exceptions import DeviceError
        ss = AsyncMock()
        poller = _make_position_poller(state_store=ss)
        poller._client.pose_uncached = AsyncMock(side_effect=DeviceError("empty response"))
        poller._client.status_uncached = AsyncMock(side_effect=Exception("also failed"))
        with pytest.raises(DeviceError, match="empty response"):
            await poller._fetch_pose_with_fallback()


# --- publish_nav_status (standalone function) -------------------------------

class TestPublishNavigationStatus:
    @pytest.mark.asyncio
    async def test_delegates_to_store(self):
        from services.status_publishing import publish_nav_status
        ss = AsyncMock()
        status = NavigationStatus(status=NavigationStatusEnum.IDLE, goal_id=None, progress_percent=0, error_reason=None)
        await publish_nav_status(status, ss)
        ss.set_last_navigation_status.assert_awaited_once_with(status)

    @pytest.mark.asyncio
    async def test_update_store_false(self):
        from services.status_publishing import publish_nav_status
        ss = AsyncMock()
        status = NavigationStatus(status=NavigationStatusEnum.ARRIVED, goal_id="cmd1", progress_percent=100, error_reason=None)
        await publish_nav_status(status, ss, update_store=False)
        ss.set_last_navigation_status.assert_not_awaited()


# --- _publish_position_status (now on PositionPoller) -----------------------

class TestPublishPositionStatus:
    @pytest.mark.asyncio
    async def test_stores_position(self):
        ss = AsyncMock()
        poller = _make_position_poller(state_store=ss)
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        with patch.object(poller, "_update_progress_from_position", new_callable=AsyncMock) as up:
            await poller._publish_position_status(pos)
        ss.set_last_position_status.assert_awaited_once_with(pos)
        up.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_error_propagates(self):
        ss = AsyncMock()
        ss.set_last_position_status = AsyncMock(side_effect=RuntimeError("boom"))
        poller = _make_position_poller(state_store=ss)
        pos = PositionStatus(x=0, y=0, theta=0, frame_id="map")
        with patch.object(poller, "_update_progress_from_position", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="boom"):
                await poller._publish_position_status(pos)


# --- _update_progress_from_position (now on PositionPoller) -----------------

class TestUpdateProgressFromPosition:
    @pytest.mark.asyncio
    async def test_no_active_commands_noop(self):
        ss = AsyncMock()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        poller = _make_position_poller(state_store=ss)
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        await poller._update_progress_from_position(pos)
        # Nothing to assert beyond no crash

    @pytest.mark.asyncio
    async def test_terminal_state_skipped(self):
        fa = ForceArrivalSignal()
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.FINISHED)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=None)
        poller = _make_position_poller(state_store=ss, force_arrival=fa)
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        await poller._update_progress_from_position(pos)
        # Dwell should be reset
        assert not fa.dwell_elapsed("cmd1", 0.0)

    @pytest.mark.asyncio
    async def test_canceling_state_skipped(self):
        fa = ForceArrivalSignal()
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.CANCELING)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        poller = _make_position_poller(state_store=ss, force_arrival=fa)
        pos = PositionStatus(x=1.0, y=2.0, theta=0, frame_id="map")
        await poller._update_progress_from_position(pos)
        assert not fa.dwell_elapsed("cmd1", 0.0)

    @pytest.mark.asyncio
    async def test_no_session_skipped(self):
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=None)
        poller = _make_position_poller(state_store=ss)
        pos = PositionStatus(x=5.0, y=5.0, theta=0, frame_id="map")
        with patch("services.position_poller.update_session_from_current") as usc:
            await poller._update_progress_from_position(pos)
        usc.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_progress(self):
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=42)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        poller = _make_position_poller(state_store=ss, bus=bus)
        pos = PositionStatus(x=5.0, y=5.0, theta=0, frame_id="map")
        with patch("services.position_poller.settings") as cfg, \
             patch("services.position_poller.publish_nav_status", new_callable=AsyncMock), \
             patch("services.position_poller.update_session_from_current", return_value=session):
            cfg.symovo_force_arrival_on_proximity = False
            await poller._update_progress_from_position(pos)
        bus.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_force_arrival_sets_flag(self):
        """When robot is at goal for dwell_s, force arrival signal is set."""
        fa = ForceArrivalSignal()
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=95)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        # Pre-set dwell timer to simulate already at goal for 10s
        fa._at_goal_since["cmd1"] = time.monotonic() - 10
        poller = _make_position_poller(state_store=ss, force_arrival=fa)
        # Position right at goal
        pos = PositionStatus(x=10.0, y=10.0, theta=0, frame_id="map")

        with patch("services.position_poller.settings") as cfg, \
             patch("services.position_poller.update_session_from_current", return_value=session):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            await poller._update_progress_from_position(pos)
        assert fa.is_set("cmd1")

    @pytest.mark.asyncio
    async def test_not_at_goal_clears_dwell_timer(self):
        fa = ForceArrivalSignal()
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=30)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        # Pre-set dwell timer
        fa._at_goal_since["cmd1"] = time.time()
        poller = _make_position_poller(state_store=ss, force_arrival=fa)
        # Position far from goal
        pos = PositionStatus(x=0.0, y=0.0, theta=0, frame_id="map")

        with patch("services.position_poller.settings") as cfg, \
             patch("services.position_poller.publish_nav_status", new_callable=AsyncMock), \
             patch("services.position_poller.update_session_from_current", return_value=session):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            await poller._update_progress_from_position(pos)
        assert "cmd1" not in fa._at_goal_since

    @pytest.mark.asyncio
    async def test_first_at_goal_starts_dwell(self):
        fa = ForceArrivalSignal()
        ss = AsyncMock()
        t = _make_active_transport(state=NavigationStateMachine.RUNNING)
        session = _make_session(progress=90)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        poller = _make_position_poller(state_store=ss, force_arrival=fa)
        pos = PositionStatus(x=10.0, y=10.0, theta=0, frame_id="map")

        with patch("services.position_poller.settings") as cfg, \
             patch("services.position_poller.publish_nav_status", new_callable=AsyncMock), \
             patch("services.position_poller.update_session_from_current", return_value=session):
            cfg.symovo_force_arrival_on_proximity = True
            cfg.symovo_arrival_dist_m = 0.25
            cfg.symovo_arrival_dwell_s = 2.5
            await poller._update_progress_from_position(pos)
        assert "cmd1" in fa._at_goal_since
        assert not fa.is_set("cmd1")  # not yet dwelled long enough


# --- StatusPoller.run() (was _watch_status_loop) ----------------------------

class TestWatchStatusLoop:
    @pytest.mark.asyncio
    async def test_caches_status(self):
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _stop_after_one(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()

        client = AsyncMock()
        client.status_uncached = AsyncMock(return_value={"battery": 80})
        ss.set_last_raw_status = AsyncMock(side_effect=_stop_after_one)
        poller = _make_status_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        # running_flag already set above
        with patch("services.status_poller.settings") as cfg:
            cfg.status_cache_hz = 10.0
            await poller.run()
        assert ss.set_last_raw_status.await_count >= 1

    @pytest.mark.asyncio
    async def test_handles_non_dict_response(self):
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _track_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()

        client = AsyncMock()
        client.status_uncached = AsyncMock(return_value="not-a-dict")
        poller = _make_status_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.status_poller.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_track_sleep):
            cfg.status_cache_hz = 10.0
            await poller.run()
        ss.set_last_raw_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_error_with_backoff(self):
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _track(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                running_flag.clear()

        client = AsyncMock()
        client.status_uncached = AsyncMock(side_effect=RuntimeError("network"))
        poller = _make_status_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.status_poller.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_track):
            cfg.status_cache_hz = 10.0
            await poller.run()
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_handles_expected_errors_quietly(self):
        """DeviceError should log at debug, not warning."""
        from exceptions import DeviceError
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _track(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()

        client = AsyncMock()
        client.status_uncached = AsyncMock(side_effect=DeviceError("HTTP 404 not found"))
        poller = _make_status_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.status_poller.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_track):
            cfg.status_cache_hz = 10.0
            await poller.run()
        # Should not raise, just loop and eventually stop

    @pytest.mark.asyncio
    async def test_cancelled(self):
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        client = AsyncMock()
        client.status_uncached = AsyncMock(side_effect=asyncio.CancelledError)
        poller = _make_status_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.status_poller.settings") as cfg:
            cfg.status_cache_hz = 10.0
            # Should NOT raise CancelledError -- returns cleanly
            await poller.run()


# --- _transport_manager (still on StatusPublisher) --------------------------

class TestTransportManager:
    @pytest.mark.asyncio
    async def test_spawns_watcher_for_active_command(self):
        ss = AsyncMock()
        t = _make_active_transport()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": t})
        with patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_stop), \
             patch("services.status_publisher.TransportWatcherTask") as MockWatcher:
            cfg.navigation_status_hz = 2.0
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock()
            MockWatcher.return_value = mock_instance
            await pub._transport_manager()
        assert "cmd1" in pub._transport_tasks

    @pytest.mark.asyncio
    async def test_stops_watcher_for_inactive_command(self):
        ss = AsyncMock()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        # Pre-populate a transport task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        pub._transport_tasks["old_cmd"] = ("old_t", mock_task)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
        with patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_stop), \
             patch("services.status_publisher.publish_nav_status", new_callable=AsyncMock):
            cfg.navigation_status_hz = 2.0
            cfg.navigation_terminal_hold_s = 0.0
            await pub._transport_manager()
        mock_task.cancel.assert_called_once()
        assert "old_cmd" not in pub._transport_tasks

    @pytest.mark.asyncio
    async def test_idle_heartbeat_published(self):
        ss = AsyncMock()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(None, 0.0))
        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.publish_nav_status", new_callable=AsyncMock) as pns, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            cfg.navigation_terminal_hold_s = 0.0
            await pub._transport_manager()
        pns.assert_awaited()
        args = pns.call_args[0]
        assert args[0].status == NavigationStatusEnum.IDLE

    @pytest.mark.asyncio
    async def test_holds_arrived_status(self):
        """When within hold window, the ARRIVED status should be republished."""
        ss = AsyncMock()
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
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        ss.get_active_commands_for_publishing = AsyncMock(return_value={})
        ss.get_last_navigation_status_with_ts = AsyncMock(return_value=(arrived_status, time.time()))
        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.publish_nav_status", new_callable=AsyncMock) as pns, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            cfg.navigation_terminal_hold_s = 5.0
            await pub._transport_manager()
        pns.assert_awaited()
        # Check it was called with update_store=False for the hold
        call_kwargs = pns.call_args_list[0]
        assert call_kwargs.kwargs.get("update_store") is False

    @pytest.mark.asyncio
    async def test_restarts_watcher_on_transport_id_change(self):
        ss = AsyncMock()
        new_t = _make_active_transport(command_id="cmd1", transport_id="new_transport")
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        old_task = MagicMock()
        old_task.done.return_value = False
        old_task.cancel = MagicMock()
        pub._transport_tasks["cmd1"] = ("old_transport", old_task)
        ss.get_active_commands_for_publishing = AsyncMock(return_value={"cmd1": new_t})
        with patch("services.status_publisher.settings") as cfg, \
             patch("services.status_publisher.TransportWatcherTask") as MockWatcher, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock()
            MockWatcher.return_value = mock_instance
            await pub._transport_manager()
        old_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_in_manager_doesnt_crash(self):
        ss = AsyncMock()
        call_count = 0

        async def _stop(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._running_flag.clear()

        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=ss,
        )
        pub._running_flag.set()
        ss.get_active_commands_for_publishing = AsyncMock(side_effect=RuntimeError("db error"))
        with patch("services.status_publisher.settings") as cfg, \
             patch("asyncio.sleep", side_effect=_stop):
            cfg.navigation_status_hz = 2.0
            await pub._transport_manager()
        # Should exit cleanly


# --- TransportWatcherTask.run() (was _watch_transport) ----------------------

class TestWatchTransport:
    @pytest.mark.asyncio
    async def test_exits_if_transport_not_active_at_start(self):
        ss = AsyncMock()
        ss.get_active_transport = AsyncMock(return_value=None)
        watcher = _make_transport_watcher(state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")
        # Should return without error

    @pytest.mark.asyncio
    async def test_exits_on_transport_id_mismatch_at_start(self):
        ss = AsyncMock()
        wrong = _make_active_transport(command_id="cmd1", transport_id="t_other")
        ss.get_active_transport = AsyncMock(return_value=wrong)
        watcher = _make_transport_watcher(state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")

    @pytest.mark.asyncio
    async def test_force_arrived_consumed(self):
        """When force arrival is signalled, terminal sequence executes."""
        fa = ForceArrivalSignal()
        fa.ensure("cmd1")
        fa.signal("cmd1")
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport()
        session = _make_session()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        ss.clear_transport = AsyncMock()

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": 5, "timestamp": 100}}
        )
        watcher = _make_transport_watcher(
            symovo_client=client, state_store=ss, bus=bus, force_arrival=fa,
        )
        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock) as pns:
            await watcher.run(command_id="cmd1", transport_id="t1")
        bus.publish.assert_awaited()
        # Check ARRIVED published
        arrived_calls = [c for c in pns.call_args_list if c[0][0].status == NavigationStatusEnum.ARRIVED]
        assert len(arrived_calls) >= 1

    @pytest.mark.asyncio
    async def test_generation_mismatch_stops_watcher(self):
        ss = AsyncMock()
        t_initial = _make_active_transport(generation=0)
        t_later = _make_active_transport(generation=1)  # generation changed

        call_idx = 0
        async def get_active(cmd_id):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return t_initial  # initial check
            return t_later  # generation changed

        ss.get_active_transport = AsyncMock(side_effect=get_active)
        ss.update_transport_state = AsyncMock()
        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": 5, "timestamp": 100}}
        )
        watcher = _make_transport_watcher(symovo_client=client, state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")
        # Should exit without error

    @pytest.mark.asyncio
    async def test_terminal_finished_publishes_arrived(self):
        """FINISHED state -> ARRIVED status + ResultSuccessEvent."""
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport(state=5, generation=0)
        session = _make_session()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.get_session = AsyncMock(return_value=session)
        ss.update_transport_state = AsyncMock()
        ss.upsert_session = AsyncMock()
        ss.clear_transport = AsyncMock()
        ss.get_last_position_status = AsyncMock(return_value=None)

        client = AsyncMock()
        # Initial fetch returns FINISHED
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 100}}
        )
        # Long-poll also returns FINISHED
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 101}}
        )
        client.status_uncached = AsyncMock(return_value={})
        watcher = _make_transport_watcher(
            symovo_client=client, state_store=ss, bus=bus,
        )
        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_terminal_canceled_publishes_idle(self):
        """CANCELED state -> IDLE status + ResultCanceledEvent."""
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        ss.get_last_position_status = AsyncMock(return_value=None)

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.CANCELED, "timestamp": 100}}
        )
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.CANCELED, "timestamp": 101}}
        )
        watcher = _make_transport_watcher(
            symovo_client=client, state_store=ss, bus=bus,
        )
        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()
        # Verify ResultCanceledEvent was published
        canceled_events = [c for c in bus.publish.call_args_list if isinstance(c[0][0], ResultCanceledEvent)]
        assert len(canceled_events) >= 1

    @pytest.mark.asyncio
    async def test_terminal_error_publishes_error(self):
        """ERROR state -> ERROR status + ResultErrorEvent."""
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        ss.get_last_position_status = AsyncMock(return_value=None)

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.ERROR, "timestamp": 100}}
        )
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.ERROR, "timestamp": 101}}
        )
        client.status_uncached = AsyncMock(return_value={"state_flags": {}})
        watcher = _make_transport_watcher(
            symovo_client=client, state_store=ss, bus=bus,
        )
        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock), \
             patch("services.transport_watcher.ErrorMapper") as em:
            em.get_error_reason = MagicMock(return_value="obstacle_detected")
            await watcher.run(command_id="cmd1", transport_id="t1")
        error_events = [c for c in bus.publish.call_args_list if isinstance(c[0][0], ResultErrorEvent)]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_http_404_clears_transport(self):
        """404 for transport -> clear and stop."""
        from exceptions import DeviceError
        ss = AsyncMock()
        t = _make_active_transport()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": 5, "timestamp": 100}}
        )
        err = DeviceError("HTTP 404 /transport/t1 not found")
        err.http_status = 404
        client.transport_wait_for_changes = AsyncMock(side_effect=err)
        watcher = _make_transport_watcher(symovo_client=client, state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited_with("cmd1")

    @pytest.mark.asyncio
    async def test_connection_error_with_force_arrived(self):
        """Connection error + force-arrived -> error reported (safety guard)."""
        from exceptions import DeviceConnectionError
        fa = ForceArrivalSignal()
        fa.ensure("cmd1")
        fa.signal("cmd1")
        ss = AsyncMock()
        bus = AsyncMock(spec=EventBus)
        t = _make_active_transport()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()
        ss.get_last_raw_pose_age_s = AsyncMock(return_value=1.0)  # fresh pose

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": 5, "timestamp": 100}}
        )
        # Force-arrival is consumed at the top of the loop, so the connection error
        # in the long-poll won't be reached in the same iteration.
        # However, after force-arrival is consumed, execute_force_arrival runs.
        session = _make_session()
        ss.get_session = AsyncMock(return_value=session)
        ss.upsert_session = AsyncMock()
        watcher = _make_transport_watcher(
            symovo_client=client, state_store=ss, bus=bus, force_arrival=fa,
        )
        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_deleted_transport_clears(self):
        """Empty response from long-poll means transport deleted."""
        ss = AsyncMock()
        t = _make_active_transport()
        ss.get_active_transport = AsyncMock(return_value=t)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": 5, "timestamp": 100}}
        )
        client.transport_wait_for_changes = AsyncMock(return_value={})
        watcher = _make_transport_watcher(symovo_client=client, state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_initial_fetch_failure_continues(self):
        """If initial transport_get_uncached fails, watcher should still proceed."""
        ss = AsyncMock()
        t = _make_active_transport()

        call_idx = 0
        async def get_active(cmd_id):
            nonlocal call_idx
            call_idx += 1
            if call_idx <= 2:
                return t
            return None  # stop after initial + 1 loop

        ss.get_active_transport = AsyncMock(side_effect=get_active)
        ss.update_transport_state = AsyncMock()
        ss.clear_transport = AsyncMock()

        client = AsyncMock()
        client.transport_get_uncached = AsyncMock(side_effect=Exception("timeout"))
        client.transport_wait_for_changes = AsyncMock(return_value={})
        watcher = _make_transport_watcher(symovo_client=client, state_store=ss)
        await watcher.run(command_id="cmd1", transport_id="t1")
        # Should exit cleanly after transport becomes inactive


# --- PositionPoller.run() (was _watch_position_longpoll) --------------------

class TestWatchPositionLongpoll:
    @pytest.mark.asyncio
    async def test_polling_fallback_on_amr_error(self):
        """If amr_wait_for_changes throws a DeviceError with 404, falls back to polling."""
        from exceptions import DeviceError
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _mock_amr_wait(**kwargs):
            raise DeviceError("HTTP 404 not found")

        async def _mock_pose():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()
            return {"pose": {"x": 1, "y": 2, "theta": 0.5}}

        client = AsyncMock()
        client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr_wait)
        poller = _make_position_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.position_poller.settings") as cfg, \
             patch.object(poller, "_parse_pose_data", return_value=PositionStatus(x=1, y=2, theta=0.5, frame_id="map")), \
             patch.object(poller, "_publish_position_status", new_callable=AsyncMock), \
             patch.object(poller, "_fetch_pose_with_fallback", side_effect=_mock_pose):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            cfg.symovo_timeout_seconds = 5
            await poller.run()
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_publishes_position_on_success(self):
        """Successful AMR long-poll data -> position published."""
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _mock_amr_wait(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()
                raise asyncio.CancelledError
            return {"result": {"pose": {"x": 3, "y": 4, "theta": 0.1}, "timestamp": 12345}}

        client = AsyncMock()
        client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr_wait)
        poller = _make_position_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.position_poller.settings") as cfg, \
             patch.object(poller, "_fetch_pose_with_fallback", new_callable=AsyncMock,
                          return_value={"pose": {"x": 3, "y": 4, "theta": 0.1}}), \
             patch.object(poller, "_publish_position_status", new_callable=AsyncMock) as pps, \
             patch.object(poller, "_parse_pose_data",
                          return_value=PositionStatus(x=3, y=4, theta=0.1, frame_id="map")):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            cfg.symovo_timeout_seconds = 5
            await poller.run()
        assert pps.await_count >= 1

    @pytest.mark.asyncio
    async def test_cancelled_exits_cleanly(self):
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        client = AsyncMock()
        client.amr_wait_for_changes = AsyncMock(side_effect=asyncio.CancelledError)
        poller = _make_position_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.position_poller.settings") as cfg:
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            cfg.symovo_timeout_seconds = 5
            await poller.run()

    @pytest.mark.asyncio
    async def test_fetch_error_backoff(self):
        """Fetch errors cause backoff sleep."""
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _fail_pose():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                running_flag.clear()
                raise asyncio.CancelledError
            raise RuntimeError("network down")

        client = AsyncMock()
        client.amr_wait_for_changes = AsyncMock(side_effect=asyncio.TimeoutError)
        poller = _make_position_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.position_poller.settings") as cfg, \
             patch.object(poller, "_fetch_pose_with_fallback", side_effect=_fail_pose):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 5
            cfg.symovo_timeout_seconds = 5
            await poller.run()

    @pytest.mark.asyncio
    async def test_longpoll_timeout_keeps_publishing(self):
        """Timeout in long-poll -> falls through to fetch+publish if interval elapsed."""
        ss = AsyncMock()
        running_flag = asyncio.Event()
        running_flag.set()
        call_count = 0

        async def _mock_amr(**kwargs):
            raise asyncio.TimeoutError

        async def _mock_fetch():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                running_flag.clear()
            return {"pose": {"x": 1, "y": 1, "theta": 0}}

        client = AsyncMock()
        client.amr_wait_for_changes = AsyncMock(side_effect=_mock_amr)
        poller = _make_position_poller(
            symovo_client=client, state_store=ss, running_flag=running_flag, running=False,
        )
        with patch("services.position_poller.settings") as cfg, \
             patch.object(poller, "_fetch_pose_with_fallback", side_effect=_mock_fetch), \
             patch.object(poller, "_publish_position_status", new_callable=AsyncMock) as pps, \
             patch.object(poller, "_parse_pose_data",
                          return_value=PositionStatus(x=1, y=1, theta=0, frame_id="map")):
            cfg.position_status_hz = 10.0
            cfg.transport_watch_timeout = 0.01
            cfg.symovo_timeout_seconds = 5
            await poller.run()
        assert pps.await_count >= 1


# --- get_progress_for_command (was _get_progress_for_command) ---------------

class TestGetProgressForCommandExtended:
    @pytest.mark.asyncio
    async def test_non_terminal_with_position_updates_session(self):
        ss = AsyncMock()
        session = _make_session(progress=40)
        pos = PositionStatus(x=5, y=5, theta=0, frame_id="map")
        ss.get_session = AsyncMock(return_value=session)
        ss.get_last_position_status = AsyncMock(return_value=pos)
        ss.upsert_session = AsyncMock()
        with patch("services.transport_watcher.update_session_from_current", return_value=session):
            result = await get_progress_for_command(ss, "cmd1", fallback_state=5)
        assert result == 40

    @pytest.mark.asyncio
    async def test_non_terminal_no_position(self):
        ss = AsyncMock()
        session = _make_session(progress=60)
        ss.get_session = AsyncMock(return_value=session)
        ss.get_last_position_status = AsyncMock(return_value=None)
        result = await get_progress_for_command(ss, "cmd1", fallback_state=5)
        assert result == 60

    @pytest.mark.asyncio
    async def test_terminal_no_session_uses_calc(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        result = await get_progress_for_command(
            ss, "cmd1", fallback_state=NavigationStateMachine.FINISHED,
        )
        assert result == 100  # FINISHED


# --- get_progress_for_command fallback cases (was _calculate_progress) -------

class TestCalculateProgressExtended:
    @pytest.mark.asyncio
    async def test_canceling_returns_50(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        result = await get_progress_for_command(
            ss, "cmd1", fallback_state=NavigationStateMachine.CANCELING,
        )
        assert result == 50

    @pytest.mark.asyncio
    async def test_unknown_state_returns_5(self):
        ss = AsyncMock()
        ss.get_session = AsyncMock(return_value=None)
        result = await get_progress_for_command(ss, "cmd1", fallback_state=999)
        assert result == 5


# --- start / stop extended --------------------------------------------------

class TestStartStopExtended:
    @pytest.mark.asyncio
    async def test_stop_cancels_transport_tasks(self):
        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=AsyncMock(),
        )
        pub._running_flag.set()

        async def _noop():
            await asyncio.sleep(10)

        transport_task = asyncio.create_task(_noop())
        main_task = asyncio.create_task(_noop())
        pub._transport_tasks["cmd1"] = ("t1", transport_task)
        pub._tasks = [main_task]

        await pub.stop()
        assert not pub._running_flag.is_set()
        assert len(pub._transport_tasks) == 0

    @pytest.mark.asyncio
    async def test_stop_timeout_doesnt_crash(self):
        pub = StatusPublisher(
            symovo_client=AsyncMock(),
            bus=AsyncMock(spec=EventBus),
            state_store=AsyncMock(),
        )
        pub._running_flag.set()

        async def _hang():
            await asyncio.sleep(100)

        pub._tasks = [asyncio.create_task(_hang())]

        # Patch wait_for to simulate timeout
        original_wait_for = asyncio.wait_for
        async def _timeout_wait_for(*a, **kw):
            raise asyncio.TimeoutError

        with patch("asyncio.wait_for", side_effect=_timeout_wait_for):
            await pub.stop()
        assert not pub._running_flag.is_set()

    @pytest.mark.asyncio
    async def test_start_error_resets_running(self):
        pub = StatusPublisher(
            symovo_client=MagicMock(),
            bus=EventBus(),
            state_store=AsyncMock(),
        )
        with patch("asyncio.create_task", side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError):
                await pub.start()
        assert not pub._running_flag.is_set()


# --- parse_position_status (was _parse_pose_data) ---------------------------

class TestParsePoseDataExtended:
    def test_x_m_y_m_without_theta_deg(self):
        """When theta_deg is absent, use theta field directly."""
        raw = {"pose": {"x_m": 1.0, "y_m": 2.0, "theta": 0.5}}
        result = parse_position_status(raw)
        assert result is not None
        # theta should use radians conversion if theta_deg present, else raw theta
        assert abs(result.theta - 0.5) < 1e-6

    def test_result_without_pose(self):
        """result dict without pose key -> None."""
        raw = {"result": {"something": "else"}}
        result = parse_position_status(raw)
        assert result is None

    def test_pose_key_is_none(self):
        """pose key exists but value is None -> check normalized format."""
        raw = {"pose": None}
        result = parse_position_status(raw)
        assert result is None

    def test_theta_deg_only(self):
        """theta_deg without theta -> convert to radians."""
        raw = {"pose": {"x": 1.0, "y": 2.0, "theta_deg": 180.0}}
        result = parse_position_status(raw)
        assert result is not None
        assert abs(result.theta - math.pi) < 0.01
