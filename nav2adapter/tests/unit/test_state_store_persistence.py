"""Tests for StateStore — persistence writer, load/save, register/clear, raw caches."""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from services.state_store import StateStore
from domain.models import ActiveTransport, NavigationStatus, NavigationStatusEnum, PositionStatus, NavigationSession, Pose2D
from services.persistence_store import PersistedCommand, PersistedSession


def _make_store(persistence=False):
    """Create a fresh StateStore for testing, optionally with persistence."""
    with patch("services.state_store.settings") as s:
        s.persistence_enabled = persistence
        s.persistence_path = "/tmp/test_state.json" if persistence else None
        store = StateStore()
    return store


# ─── Persistence writer loop ─────────────────────────────────────────

class TestPersistenceWriterLoop:
    @pytest.mark.asyncio
    async def test_writer_processes_upsert(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.upsert = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        cmd = PersistedCommand(
            command_id="c1", transport_id="t1", target_id="posA",
            last_state=5, last_result=None, created_at="now", updated_at="now",
        )
        await store._persistence_queue.put({"type": "upsert", "data": cmd})

        async def _stop_after_one():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        stopper = asyncio.create_task(_stop_after_one())
        await asyncio.gather(task, stopper)
        store._persistence.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_writer_processes_delete(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.delete = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        await store._persistence_queue.put({"type": "delete", "command_id": "c1"})

        async def _stop():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.create_task(_stop())
        await task
        store._persistence.delete.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_writer_processes_upsert_session(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.upsert_session = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        sess = PersistedSession(
            command_id="c1", target_id="posA",
            start={"x": 0, "y": 0}, goal={"x": 10, "y": 10},
            total_dist_m=14.14, min_remaining_dist_m=7.0,
            progress_percent=50, created_at="now", updated_at="now",
        )
        await store._persistence_queue.put({"type": "upsert_session", "data": sess})

        async def _stop():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.create_task(_stop())
        await task
        store._persistence.upsert_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_writer_processes_delete_session(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.delete_session = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        await store._persistence_queue.put({"type": "delete_session", "command_id": "c1"})

        async def _stop():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.create_task(_stop())
        await task
        store._persistence.delete_session.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_writer_handles_error(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.upsert = AsyncMock(side_effect=RuntimeError("disk"))
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        cmd = PersistedCommand(
            command_id="c1", transport_id="t1", target_id="posA",
            last_state=5, last_result=None, created_at="now", updated_at="now",
        )
        await store._persistence_queue.put({"type": "upsert", "data": cmd})

        async def _stop():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.create_task(_stop())
        await task
        # Should not crash

    @pytest.mark.asyncio
    async def test_writer_cancelled(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Should exit cleanly (CancelledError is caught internally)

    @pytest.mark.asyncio
    async def test_writer_unknown_op(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence_running = True
        store._persistence_queue = asyncio.Queue()

        await store._persistence_queue.put({"type": "unknown_thing"})

        async def _stop():
            await asyncio.sleep(0.05)
            store._persistence_running = False

        task = asyncio.create_task(store._persistence_writer_loop())
        await asyncio.create_task(_stop())
        await task

    @pytest.mark.asyncio
    async def test_writer_no_queue(self):
        store = _make_store()
        store._persistence_queue = None
        await store._persistence_writer_loop()  # should return immediately


# ─── _enqueue_persistence ────────────────────────────────────────────

class TestEnqueuePersistence:
    def test_enqueue_with_queue(self):
        store = _make_store()
        store._persistence_queue = asyncio.Queue()
        store._enqueue_persistence({"type": "upsert", "data": "test"})
        assert not store._persistence_queue.empty()

    def test_enqueue_no_queue(self):
        store = _make_store()
        store._persistence_queue = None
        store._enqueue_persistence({"type": "upsert"})  # should not crash

    def test_enqueue_queue_full(self):
        store = _make_store()
        store._persistence_queue = asyncio.Queue(maxsize=1)
        store._persistence_queue.put_nowait({"type": "old"})
        store._enqueue_persistence({"type": "new"})  # should not crash, just drop


# ─── start/stop persistence ──────────────────────────────────────────

class TestStartStopPersistence:
    @pytest.mark.asyncio
    async def test_start_creates_queue_and_task(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        await store.start_persistence()
        assert store._persistence_running
        assert store._persistence_queue is not None
        assert store._persistence_task is not None
        store._persistence_running = False
        store._persistence_task.cancel()
        await asyncio.gather(store._persistence_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_start_no_persistence(self):
        store = _make_store()
        store._persistence = None
        await store.start_persistence()
        assert not store._persistence_running

    @pytest.mark.asyncio
    async def test_stop_drains_queue(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.upsert = AsyncMock()
        await store.start_persistence()

        cmd = PersistedCommand(
            command_id="c1", transport_id="t1", target_id="posA",
            last_state=5, last_result=None, created_at="now", updated_at="now",
        )
        store._enqueue_persistence({"type": "upsert", "data": cmd})
        await store.stop_persistence()
        assert store._persistence_task is None

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        store = _make_store()
        await store.stop_persistence()  # no task, should not crash


# ─── load_from_persistence ────────────────────────────────────────────

class TestLoadFromPersistence:
    @pytest.mark.asyncio
    async def test_no_persistence(self):
        store = _make_store()
        result = await store.load_from_persistence()
        assert result == 0

    @pytest.mark.asyncio
    async def test_loads_sessions(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.load_sessions = AsyncMock(return_value={
            "cmd1": PersistedSession(
                command_id="cmd1", target_id="posA",
                start={"x": 0, "y": 0, "map_id": None},
                goal={"x": 10, "y": 10, "map_id": None},
                total_dist_m=14.14, min_remaining_dist_m=7.0,
                progress_percent=50,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        })
        result = await store.load_from_persistence()
        assert result == 1
        session = await store.get_session("cmd1")
        assert session is not None
        assert session.progress_percent == 50

    @pytest.mark.asyncio
    async def test_load_corrupt_session_skipped(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()
        store._persistence.load_sessions = AsyncMock(return_value={
            "cmd1": PersistedSession(
                command_id="cmd1", target_id="posA",
                start="not_a_dict", goal="not_a_dict",
                total_dist_m=14.14, min_remaining_dist_m=7.0,
                progress_percent=50, created_at="invalid", updated_at="invalid",
            ),
        })
        result = await store.load_from_persistence()
        assert result == 0


# ─── get_persisted_commands_for_recovery ──────────────────────────────

class TestGetPersistedCommandsForRecovery:
    @pytest.mark.asyncio
    async def test_no_persistence(self):
        store = _make_store()
        result = await store.get_persisted_commands_for_recovery()
        assert result == {}

    @pytest.mark.asyncio
    async def test_filters_by_transport_id(self):
        store = _make_store(persistence=True)
        store._persistence = AsyncMock()

        cmd_with = PersistedCommand(
            command_id="c1", transport_id="t1", target_id="posA",
            last_state=5, last_result=None, created_at="now", updated_at="now",
        )
        cmd_without = PersistedCommand(
            command_id="c2", transport_id=None, target_id="posB",
            last_state=0, last_result=None, created_at="now", updated_at="now",
        )
        store._persistence.load = AsyncMock(return_value={"c1": cmd_with, "c2": cmd_without})
        result = await store.get_persisted_commands_for_recovery()
        assert "c1" in result
        assert "c2" not in result


# ─── register + clear + navigation status ─────────────────────────────

class TestRegisterAndClear:
    @pytest.mark.asyncio
    async def test_register_and_get_transport(self):
        store = _make_store()
        t = await store.register_command("c1", "t1", 5, "posA")
        assert t.command_id == "c1"
        assert t.generation == 0
        # Re-register increments generation
        t2 = await store.register_command("c1", "t2", 5, "posA")
        assert t2.generation == 1

    @pytest.mark.asyncio
    async def test_clear_transport(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        cleared = await store.clear_transport("c1")
        assert cleared is True
        t = await store.get_active_transport("c1")
        assert t is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent(self):
        store = _make_store()
        cleared = await store.clear_transport("c_missing")
        assert cleared is False

    @pytest.mark.asyncio
    async def test_current_command_id(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        cid = await store.get_current_command_id()
        assert cid == "c1"

    @pytest.mark.asyncio
    async def test_clear_all_commands(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        await store.register_command("c2", "t2", 5)
        count = await store.clear_all_commands()
        assert count == 2
        cid = await store.get_current_command_id()
        assert cid is None

    @pytest.mark.asyncio
    async def test_get_active_commands_for_publishing(self):
        store = _make_store()
        result = await store.get_active_commands_for_publishing()
        assert result == {}
        await store.register_command("c1", "t1", 5)
        result = await store.get_active_commands_for_publishing()
        assert "c1" in result

    @pytest.mark.asyncio
    async def test_get_active_transport_by_transport_id(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        t = await store.get_active_transport_by_transport_id("t1")
        assert t is not None
        assert t.command_id == "c1"
        t2 = await store.get_active_transport_by_transport_id("t999")
        assert t2 is None

    @pytest.mark.asyncio
    async def test_update_transport_state(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        t = await store.update_transport_state("c1", 8)
        assert t is not None
        assert t.state == 8

    @pytest.mark.asyncio
    async def test_update_transport_state_nonexistent(self):
        store = _make_store()
        t = await store.update_transport_state("c_missing", 8)
        assert t is None


# ─── Navigation and position status ──────────────────────────────────

class TestStatusCache:
    @pytest.mark.asyncio
    async def test_navigation_status(self):
        store = _make_store()
        status = NavigationStatus(
            status=NavigationStatusEnum.NAVIGATING,
            goal_id="c1",
            progress_percent=50,
            error_reason=None,
        )
        await store.set_last_navigation_status(status)
        result = await store.get_last_navigation_status()
        assert result.status == NavigationStatusEnum.NAVIGATING

    @pytest.mark.asyncio
    async def test_navigation_status_with_ts(self):
        store = _make_store()
        status = NavigationStatus(
            status=NavigationStatusEnum.IDLE,
            goal_id=None,
            progress_percent=0,
        )
        await store.set_last_navigation_status(status)
        result, ts = await store.get_last_navigation_status_with_ts()
        assert result is not None
        assert ts > 0

    @pytest.mark.asyncio
    async def test_position_status(self):
        store = _make_store()
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        await store.set_last_position_status(pos)
        result = await store.get_last_position_status()
        assert result.x == 1.0

    @pytest.mark.asyncio
    async def test_raw_pose_cache(self):
        store = _make_store()
        await store.set_last_raw_pose({"x": 1, "y": 2, "theta": 0.5})
        result = await store.get_last_raw_pose()
        assert result["x"] == 1
        age = await store.get_last_raw_pose_age_s()
        assert age < 1.0

    @pytest.mark.asyncio
    async def test_raw_pose_age_never_updated(self):
        store = _make_store()
        age = await store.get_last_raw_pose_age_s()
        assert age == float("inf")

    @pytest.mark.asyncio
    async def test_raw_status_cache(self):
        store = _make_store()
        await store.set_last_raw_status({"battery": 80})
        result = await store.get_last_raw_status()
        assert result["battery"] == 80
        age = await store.get_last_raw_status_age_s()
        assert age < 1.0

    @pytest.mark.asyncio
    async def test_raw_status_age_never_updated(self):
        store = _make_store()
        age = await store.get_last_raw_status_age_s()
        assert age == float("inf")


# ─── Sessions ────────────────────────────────────────────────────────

class TestSessions:
    @pytest.mark.asyncio
    async def test_upsert_and_get_session(self):
        store = _make_store()
        session = NavigationSession(
            command_id="c1",
            target_id="posA",
            start=Pose2D(x=0, y=0, map_id=None),
            goal=Pose2D(x=10, y=10, map_id=None),
            total_dist_m=14.14,
            min_remaining_dist_m=7.0,
            progress_percent=50,
        )
        await store.upsert_session(session)
        result = await store.get_session("c1")
        assert result is not None
        assert result.progress_percent == 50

    @pytest.mark.asyncio
    async def test_clear_session(self):
        store = _make_store()
        session = NavigationSession(
            command_id="c1",
            target_id="posA",
            start=Pose2D(x=0, y=0, map_id=None),
            goal=Pose2D(x=10, y=10, map_id=None),
            total_dist_m=14.14,
            min_remaining_dist_m=7.0,
            progress_percent=50,
        )
        await store.upsert_session(session)
        await store.clear_session("c1")
        result = await store.get_session("c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_active_commands(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        await store.register_command("c2", "t2", 3)
        all_cmds = await store.get_all_active_commands()
        assert len(all_cmds) == 2

    @pytest.mark.asyncio
    async def test_clear_all(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        await store.set_last_navigation_status(
            NavigationStatus(status=NavigationStatusEnum.IDLE, goal_id=None, progress_percent=0)
        )
        await store.set_last_position_status(PositionStatus(x=0, y=0, theta=0, frame_id="map"))
        await store.set_last_raw_pose({"x": 1})
        await store.set_last_raw_status({"battery": 80})
        await store.clear_all()
        assert await store.get_last_navigation_status() is None
        assert await store.get_last_position_status() is None
        assert await store.get_last_raw_pose() is None
        assert await store.get_last_raw_status() is None
        assert await store.get_current_command_id() is None

    @pytest.mark.asyncio
    async def test_set_last_result(self):
        store = _make_store(persistence=True)
        store._persistence = MagicMock()
        store._persistence_queue = asyncio.Queue()
        await store.register_command("c1", "t1", 5)
        await store.set_last_result("c1", {"status": "success"})
        assert not store._persistence_queue.empty()

    @pytest.mark.asyncio
    async def test_set_last_result_no_transport(self):
        store = _make_store()
        await store.set_last_result("c_missing", {"status": "fail"})
        # Should not crash


# ─── clear_transport picks next best ──────────────────────────────────

class TestClearTransportFallback:
    @pytest.mark.asyncio
    async def test_clear_current_picks_next(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        await store.register_command("c2", "t2", 5)
        # c2 is current
        assert await store.get_current_command_id() == "c2"
        await store.clear_transport("c2")
        # c1 should become current
        assert await store.get_current_command_id() == "c1"

    @pytest.mark.asyncio
    async def test_clear_last_sets_none(self):
        store = _make_store()
        await store.register_command("c1", "t1", 5)
        await store.clear_transport("c1")
        assert await store.get_current_command_id() is None
