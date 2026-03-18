"""
Tests for TransportWatcherTask and its helper functions.

Covers constructor wiring, terminal-state handling (FINISHED / ERROR / CANCELED),
generation-token mismatch, force-arrival integration, network-error resilience,
and running-flag shutdown.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.events import ResultCanceledEvent, ResultErrorEvent, ResultSuccessEvent
from domain.models import ActiveTransport, NavigationSession, NavigationStatusEnum
from domain.state_machine import NavigationStateMachine
from services.event_bus import EventBus
from services.force_arrival import ForceArrivalSignal
from services.transport_watcher import (
    TransportWatcherTask,
    check_transport_active,
    execute_force_arrival,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COMMAND_ID = "cmd-001"
TRANSPORT_ID = "tp-100"


def _make_active_transport(
    *,
    state: int = NavigationStateMachine.RUNNING,
    generation: int = 1,
) -> ActiveTransport:
    return ActiveTransport(
        command_id=COMMAND_ID,
        transport_id=TRANSPORT_ID,
        state=state,
        created_at=datetime.now(timezone.utc),
        target_id="pos_A",
        generation=generation,
    )


def _make_state_store(
    *,
    active_transport: ActiveTransport | None = None,
    session: NavigationSession | None = None,
) -> MagicMock:
    store = MagicMock()
    store.get_active_transport = AsyncMock(return_value=active_transport)
    store.update_transport_state = AsyncMock(return_value=active_transport)
    store.clear_transport = AsyncMock()
    store.get_session = AsyncMock(return_value=session)
    store.upsert_session = AsyncMock()
    store.get_last_navigation_status = AsyncMock(return_value=None)
    store.set_last_navigation_status = AsyncMock()
    store.get_last_position_status = AsyncMock(return_value=None)
    store.get_last_raw_pose_age_s = AsyncMock(return_value=0.0)
    return store


def _make_symovo_client() -> MagicMock:
    client = MagicMock()
    client.transport_get_uncached = AsyncMock(return_value={"result": {"state": NavigationStateMachine.RUNNING, "timestamp": 1}})
    client.transport_wait_for_changes = AsyncMock(return_value={"result": {"state": NavigationStateMachine.RUNNING, "timestamp": 2}})
    client.status_uncached = AsyncMock(return_value={"state_flags": {}})
    client.close = AsyncMock()
    return client


def _make_watcher(
    *,
    symovo_client: MagicMock | None = None,
    bus: EventBus | None = None,
    state_store: MagicMock | None = None,
    force_arrival: ForceArrivalSignal | None = None,
    running_flag: asyncio.Event | None = None,
) -> TransportWatcherTask:
    if running_flag is None:
        running_flag = asyncio.Event()
        running_flag.set()
    return TransportWatcherTask(
        symovo_client=symovo_client or _make_symovo_client(),
        bus=bus or EventBus(queue_size=100),
        state_store=state_store or _make_state_store(active_transport=_make_active_transport()),
        force_arrival=force_arrival or ForceArrivalSignal(),
        running_flag=running_flag,
    )


async def _drain_queue(q: asyncio.Queue, *, event_type=None) -> list:
    """Drain all events from a bus subscriber queue, optionally filtering by type."""
    events = []
    while not q.empty():
        ev = q.get_nowait()
        if event_type is None or isinstance(ev, event_type):
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 1. Constructor accepts all required dependencies
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_accepts_all_dependencies(self):
        flag = asyncio.Event()
        flag.set()
        client = _make_symovo_client()
        bus = EventBus(queue_size=100)
        store = _make_state_store(active_transport=_make_active_transport())
        fa = ForceArrivalSignal()

        watcher = TransportWatcherTask(
            symovo_client=client,
            bus=bus,
            state_store=store,
            force_arrival=fa,
            running_flag=flag,
        )
        assert watcher._client is client
        assert watcher._bus is bus
        assert watcher._store is store
        assert watcher._fa is fa
        assert watcher._running is flag


# ---------------------------------------------------------------------------
# 2-4. Terminal state transitions (FINISHED / ERROR / CANCELED)
# ---------------------------------------------------------------------------

class TestTerminalStates:
    """State transitions from RUNNING to terminal states trigger correct handling."""

    @pytest.mark.asyncio
    async def test_finished_triggers_success_event(self):
        """RUNNING -> FINISHED publishes ResultSuccessEvent and clears transport."""
        bus = EventBus(queue_size=100)
        q = await bus.subscribe()

        transport = _make_active_transport(state=NavigationStateMachine.RUNNING)
        store = _make_state_store(active_transport=transport)

        client = _make_symovo_client()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.RUNNING, "timestamp": 1}},
        )
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 2}},
        )

        watcher = _make_watcher(symovo_client=client, bus=bus, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID)

        events = await _drain_queue(q, event_type=ResultSuccessEvent)
        assert len(events) >= 1
        assert events[0].command_id == COMMAND_ID
        store.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_error_triggers_error_event(self):
        """RUNNING -> ERROR publishes ResultErrorEvent and clears transport."""
        bus = EventBus(queue_size=100)
        q = await bus.subscribe()

        transport = _make_active_transport(state=NavigationStateMachine.RUNNING)
        store = _make_state_store(active_transport=transport)

        client = _make_symovo_client()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.RUNNING, "timestamp": 1}},
        )
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.ERROR, "timestamp": 2}},
        )

        watcher = _make_watcher(symovo_client=client, bus=bus, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID)

        events = await _drain_queue(q, event_type=ResultErrorEvent)
        assert len(events) >= 1
        assert events[0].command_id == COMMAND_ID
        store.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_canceled_triggers_canceled_event(self):
        """RUNNING -> CANCELED publishes ResultCanceledEvent and clears transport."""
        bus = EventBus(queue_size=100)
        q = await bus.subscribe()

        transport = _make_active_transport(state=NavigationStateMachine.RUNNING)
        store = _make_state_store(active_transport=transport)

        client = _make_symovo_client()
        client.transport_get_uncached = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.RUNNING, "timestamp": 1}},
        )
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.CANCELED, "timestamp": 2}},
        )

        watcher = _make_watcher(symovo_client=client, bus=bus, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID)

        events = await _drain_queue(q, event_type=ResultCanceledEvent)
        assert len(events) >= 1
        assert events[0].command_id == COMMAND_ID
        store.clear_transport.assert_awaited()


# ---------------------------------------------------------------------------
# 5. Terminal detection exits the watcher run loop
# ---------------------------------------------------------------------------

class TestLoopExit:
    @pytest.mark.asyncio
    async def test_terminal_state_exits_run_loop(self):
        """After detecting a terminal state the watcher returns (does not loop forever)."""
        transport = _make_active_transport()
        store = _make_state_store(active_transport=transport)

        client = _make_symovo_client()
        client.transport_wait_for_changes = AsyncMock(
            return_value={"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 5}},
        )

        watcher = _make_watcher(symovo_client=client, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            # Should complete (not hang). We rely on asyncio test timeout.
            await asyncio.wait_for(
                watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID),
                timeout=5.0,
            )

        # If we got here the loop exited. Verify transport was cleaned up.
        store.clear_transport.assert_awaited()


# ---------------------------------------------------------------------------
# 6. Generation token mismatch causes early exit
# ---------------------------------------------------------------------------

class TestGenerationMismatch:
    @pytest.mark.asyncio
    async def test_generation_change_stops_watcher(self):
        """If the generation token changes between iterations, the watcher exits."""
        transport_gen1 = _make_active_transport(generation=1)
        transport_gen2 = _make_active_transport(generation=2)

        store = _make_state_store(active_transport=transport_gen1)
        # First call (initial fetch) returns gen=1, subsequent check returns gen=2.
        store.get_active_transport = AsyncMock(
            side_effect=[transport_gen1, transport_gen2],
        )

        client = _make_symovo_client()
        # The long-poll should never be reached because the generation mismatch
        # in check_transport_active breaks out before poll.
        client.transport_wait_for_changes = AsyncMock(
            side_effect=AssertionError("should not be called"),
        )

        watcher = _make_watcher(symovo_client=client, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await asyncio.wait_for(
                watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID),
                timeout=5.0,
            )

        # Watcher exited without crashing.


# ---------------------------------------------------------------------------
# 7. Force arrival signal integration
# ---------------------------------------------------------------------------

class TestForceArrival:
    @pytest.mark.asyncio
    async def test_force_arrival_publishes_arrived(self):
        """When force-arrival signal is consumed, publish ResultSuccessEvent + ARRIVED status."""
        bus = EventBus(queue_size=100)
        q = await bus.subscribe()

        transport = _make_active_transport()
        store = _make_state_store(active_transport=transport)

        fa = ForceArrivalSignal()
        fa.ensure(COMMAND_ID)
        fa.signal(COMMAND_ID)  # pre-set signal before watcher loop

        client = _make_symovo_client()
        # Long-poll should not be reached because force-arrival fires first.
        client.transport_wait_for_changes = AsyncMock(
            side_effect=AssertionError("should not be called -- force arrival should fire first"),
        )

        watcher = _make_watcher(
            symovo_client=client, bus=bus, state_store=store, force_arrival=fa,
        )

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock) as mock_pub:
            await asyncio.wait_for(
                watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID),
                timeout=5.0,
            )
            # Verify ARRIVED status was published
            arrived_calls = [
                c for c in mock_pub.call_args_list
                if c.args[0].status == NavigationStatusEnum.ARRIVED
            ]
            assert len(arrived_calls) >= 1

        events = await _drain_queue(q, event_type=ResultSuccessEvent)
        assert len(events) >= 1
        assert events[0].command_id == COMMAND_ID
        store.clear_transport.assert_awaited()


# ---------------------------------------------------------------------------
# 8. Network error during polling handled gracefully
# ---------------------------------------------------------------------------

class TestNetworkError:
    @pytest.mark.asyncio
    async def test_network_error_does_not_crash(self):
        """An OSError during long-poll does not crash the watcher; it backs off and retries."""
        transport = _make_active_transport()
        store = _make_state_store(active_transport=transport)

        call_count = 0

        async def _poll_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OSError("simulated network failure")
            # Third call returns a terminal state to end the loop.
            return {"result": {"state": NavigationStateMachine.FINISHED, "timestamp": 99}}

        client = _make_symovo_client()
        client.transport_wait_for_changes = AsyncMock(side_effect=_poll_side_effect)

        watcher = _make_watcher(symovo_client=client, state_store=store)

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock), \
             patch("asyncio.sleep", new_callable=AsyncMock):  # skip real backoff delay
            await asyncio.wait_for(
                watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID),
                timeout=10.0,
            )

        # Watcher survived the errors and eventually processed the terminal state.
        assert call_count == 3
        store.clear_transport.assert_awaited()


# ---------------------------------------------------------------------------
# 9. Running flag cleared stops the watcher
# ---------------------------------------------------------------------------

class TestRunningFlag:
    @pytest.mark.asyncio
    async def test_clearing_flag_stops_watcher(self):
        """Clearing the running_flag Event causes the watcher to exit its loop."""
        transport = _make_active_transport()
        store = _make_state_store(active_transport=transport)

        running_flag = asyncio.Event()
        running_flag.set()

        call_count = 0

        async def _poll_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # After the first poll, clear the flag so the loop terminates.
            running_flag.clear()
            return {"result": {"state": NavigationStateMachine.RUNNING, "timestamp": call_count}}

        client = _make_symovo_client()
        client.transport_wait_for_changes = AsyncMock(side_effect=_poll_side_effect)

        watcher = _make_watcher(
            symovo_client=client, state_store=store, running_flag=running_flag,
        )

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await asyncio.wait_for(
                watcher.run(command_id=COMMAND_ID, transport_id=TRANSPORT_ID),
                timeout=5.0,
            )

        # The watcher should have exited after the flag was cleared.
        assert call_count == 1


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class TestCheckTransportActive:
    @pytest.mark.asyncio
    async def test_returns_true_when_active_and_matching(self):
        transport = _make_active_transport(generation=1)
        store = _make_state_store(active_transport=transport)

        result = await check_transport_active(store, COMMAND_ID, TRANSPORT_ID, 1, "test")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_active_transport(self):
        store = _make_state_store(active_transport=None)

        result = await check_transport_active(store, COMMAND_ID, TRANSPORT_ID, 1, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_transport_id_differs(self):
        transport = _make_active_transport()
        transport.transport_id = "other-transport"
        store = _make_state_store(active_transport=transport)

        result = await check_transport_active(store, COMMAND_ID, TRANSPORT_ID, 1, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_generation_differs(self):
        transport = _make_active_transport(generation=2)
        store = _make_state_store(active_transport=transport)

        result = await check_transport_active(store, COMMAND_ID, TRANSPORT_ID, 1, "test")
        assert result is False


class TestExecuteForceArrival:
    @pytest.mark.asyncio
    async def test_publishes_success_and_clears_transport(self):
        bus = EventBus(queue_size=100)
        q = await bus.subscribe()

        store = _make_state_store()
        client = _make_symovo_client()

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock) as mock_pub:
            await execute_force_arrival(COMMAND_ID, store, bus, client)

            arrived_calls = [
                c for c in mock_pub.call_args_list
                if c.args[0].status == NavigationStatusEnum.ARRIVED
            ]
            assert len(arrived_calls) == 1

        events = await _drain_queue(q, event_type=ResultSuccessEvent)
        assert len(events) == 1
        assert events[0].command_id == COMMAND_ID
        store.clear_transport.assert_awaited_once_with(COMMAND_ID)

    @pytest.mark.asyncio
    async def test_updates_session_progress_to_100(self):
        bus = EventBus(queue_size=100)
        session = MagicMock()
        session.progress_percent = 50
        session.min_remaining_dist_m = 1.5

        store = _make_state_store(session=session)
        client = _make_symovo_client()

        with patch("services.transport_watcher.publish_nav_status", new_callable=AsyncMock):
            await execute_force_arrival(COMMAND_ID, store, bus, client)

        assert session.progress_percent == 100
        assert session.min_remaining_dist_m == 0.0
        store.upsert_session.assert_awaited_once_with(session)
