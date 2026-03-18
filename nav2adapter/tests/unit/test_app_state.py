"""Tests for app/state.py — startup, shutdown, _spawn_bg_task, RecoveryService."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace
from fastapi import FastAPI

from app.state import _spawn_bg_task, startup, shutdown
from services.recovery_service import RecoveryService


# --- _spawn_bg_task --------------------------------------------------------

class TestSpawnBgTask:
    @pytest.mark.asyncio
    async def test_creates_task_and_tracks_it(self):
        app = FastAPI()
        app.state._bg_tasks = []

        async def _noop():
            pass

        task = _spawn_bg_task(app, _noop(), "test_task")
        assert task in app.state._bg_tasks
        await task
        # After completion, done callback should remove it
        await asyncio.sleep(0.01)
        assert task not in app.state._bg_tasks

    @pytest.mark.asyncio
    async def test_failing_task_logs_error(self):
        app = FastAPI()

        async def _fail():
            raise RuntimeError("boom")

        task = _spawn_bg_task(app, _fail(), "failing_task")
        # Wait for task to complete
        await asyncio.sleep(0.05)
        assert task not in app.state._bg_tasks

    @pytest.mark.asyncio
    async def test_cancelled_task(self):
        app = FastAPI()

        async def _hang():
            await asyncio.sleep(100)

        task = _spawn_bg_task(app, _hang(), "hanging_task")
        task.cancel()
        await asyncio.sleep(0.05)
        assert task not in app.state._bg_tasks

    @pytest.mark.asyncio
    async def test_initializes_bg_tasks_list(self):
        app = FastAPI()
        # Remove _bg_tasks if it exists
        if hasattr(app.state, "_bg_tasks"):
            delattr(app.state, "_bg_tasks")

        async def _noop():
            pass

        task = _spawn_bg_task(app, _noop(), "init_task")
        assert hasattr(app.state, "_bg_tasks")
        await task


# --- startup ---------------------------------------------------------------

class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_basic(self):
        """Startup creates services and stores them on app.state."""
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.start_persistence = AsyncMock()
        mock_ss.load_from_persistence = AsyncMock(return_value=0)

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient") as sym_cls, \
             patch("app.state.StateStore", return_value=mock_ss), \
             patch("app.state.EventBus"), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.EventStreamService") as es_cls:
            cfg.settings.uvicorn_workers = 1
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.settings.startup_blocking_clear_transports = False
            cfg.log_config_summary = MagicMock()

            ed_instance = AsyncMock()
            ed_cls.return_value = ed_instance
            sp_instance = AsyncMock()
            sp_cls.return_value = sp_instance
            es_instance = AsyncMock()
            es_cls.return_value = es_instance

            await startup(app)

        assert hasattr(app.state, "services")
        assert hasattr(app.state, "symovo_client")

    @pytest.mark.asyncio
    async def test_startup_rejects_multi_worker(self):
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.start_persistence = AsyncMock()

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.StateStore", return_value=mock_ss), \
             patch("app.state.EventBus"):
            cfg.settings.uvicorn_workers = 4
            cfg.log_config_summary = MagicMock()
            with pytest.raises(RuntimeError, match="UVICORN_WORKERS"):
                await startup(app)

    @pytest.mark.asyncio
    async def test_startup_persistence_fail_continues(self):
        """Persistence start failure should not block startup."""
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.start_persistence = AsyncMock(side_effect=RuntimeError("disk full"))
        mock_ss.load_from_persistence = AsyncMock(return_value=0)

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.StateStore", return_value=mock_ss), \
             patch("app.state.EventBus"), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.EventStreamService") as es_cls:
            cfg.settings.uvicorn_workers = 1
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.log_config_summary = MagicMock()
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()
            es_cls.return_value = AsyncMock()

            await startup(app)
        # Should succeed despite persistence failure

    @pytest.mark.asyncio
    async def test_startup_clear_transports_blocking(self):
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.start_persistence = AsyncMock()
        mock_ss.load_from_persistence = AsyncMock(return_value=0)
        mock_ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient") as sym_cls, \
             patch("app.state.StateStore", return_value=mock_ss), \
             patch("app.state.EventBus"), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.EventStreamService") as es_cls:
            cfg.settings.uvicorn_workers = 1
            cfg.settings.symovo_clear_transports_on_startup = True
            cfg.settings.startup_blocking_clear_transports = True
            cfg.settings.startup_blocking_recovery = True
            cfg.settings.clear_transports_timeout_s = 30.0
            cfg.settings.state_recovery_timeout_s = 60.0
            cfg.log_config_summary = MagicMock()
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()
            es_cls.return_value = AsyncMock()
            sym_cls.return_value.clear_all_transports = AsyncMock(return_value=[])

            await startup(app)
        sym_cls.return_value.clear_all_transports.assert_awaited_once()


# --- shutdown --------------------------------------------------------------

class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_with_services(self):
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.stop_persistence = AsyncMock()
        services = MagicMock()
        services.stop = AsyncMock()
        services.state_store = mock_ss
        app.state.services = services
        with patch("app.cache.cancel_eviction_task", MagicMock()):
            await shutdown(app)
        services.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_services(self):
        """Legacy shutdown path when services container is not available."""
        app = FastAPI()
        app.state.status_publisher = MagicMock()
        app.state.status_publisher.stop = AsyncMock()
        app.state.event_dispatcher = MagicMock()
        app.state.event_dispatcher.stop = AsyncMock()
        app.state.symovo_client = MagicMock()
        app.state.symovo_client.close = AsyncMock()
        with patch("app.cache.cancel_eviction_task", MagicMock()):
            await shutdown(app)
        app.state.status_publisher.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_persistence_error(self):
        """Persistence stop failure should not crash shutdown."""
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.stop_persistence = AsyncMock(side_effect=RuntimeError("disk error"))
        services = MagicMock()
        services.stop = AsyncMock()
        services.state_store = mock_ss
        app.state.services = services
        with patch("app.cache.cancel_eviction_task", MagicMock()):
            await shutdown(app)

    @pytest.mark.asyncio
    async def test_shutdown_during_active_navigation(self):
        """Shutdown during active navigation: services.stop() is called even with active bg tasks."""
        app = FastAPI()
        mock_ss = MagicMock()
        mock_ss.stop_persistence = AsyncMock()

        # Simulate a long-running transport watcher task
        hang_task = asyncio.create_task(asyncio.sleep(3600))

        services = MagicMock()
        services.stop = AsyncMock()
        services.state_store = mock_ss
        app.state.services = services

        with patch("app.cache.cancel_eviction_task", MagicMock()):
            await shutdown(app)

        services.stop.assert_awaited_once()
        # The task should still be pending (services.stop is mocked, not real AppServices.stop)
        hang_task.cancel()
        try:
            await hang_task
        except asyncio.CancelledError:
            pass


# --- RecoveryService -------------------------------------------------------

class TestRecoverState:
    @pytest.mark.asyncio
    async def test_no_persisted_commands(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        assert result.recovered == 0

    @pytest.mark.asyncio
    async def test_recover_active_command(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        transport_data = {"state": 5}  # RUNNING
        symovo.transport_get = AsyncMock(return_value=transport_data)
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.get_current_command_id = AsyncMock(return_value=None)
        ss.register_command = AsyncMock()
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.register_command.assert_awaited_once()
        assert result.recovered == 1

    @pytest.mark.asyncio
    async def test_recover_terminal_finished(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 8})  # FINISHED
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()
        assert result.terminal == 1

    @pytest.mark.asyncio
    async def test_recover_terminal_canceled(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 6})  # CANCELED
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()
        assert result.terminal == 1

    @pytest.mark.asyncio
    async def test_recover_terminal_error(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 7})  # ERROR
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_not_found_404(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        err = Exception("not found")
        err.http_status = 404
        symovo.transport_get = AsyncMock(side_effect=err)
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()
        assert result.not_found == 1

    @pytest.mark.asyncio
    async def test_recover_timeout(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(side_effect=asyncio.TimeoutError)
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()
        assert result.not_found == 1

    @pytest.mark.asyncio
    async def test_recover_non_dict_transport(self):
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value="not a dict")
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_skips_if_new_command_active(self):
        """If a new command is already active, skip recovery of old command."""
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 5})  # RUNNING
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"old_cmd": cmd})
        ss.get_current_command_id = AsyncMock(return_value="new_cmd")
        ss.clear_transport = AsyncMock()
        ss.register_command = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        ss.register_command.assert_not_awaited()
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_multiple_commands(self):
        """Multiple persisted commands -- parallel recovery."""
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd1 = SimpleNamespace(transport_id="t1", target_id="posA")
        cmd2 = SimpleNamespace(transport_id="t2", target_id="posB")
        symovo.transport_get = AsyncMock(side_effect=[
            {"state": 8},  # FINISHED
            {"state": 5},  # RUNNING
        ])
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd1, "cmd2": cmd2})
        ss.clear_transport = AsyncMock()
        ss.get_current_command_id = AsyncMock(return_value=None)
        ss.register_command = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        assert result.terminal == 1
        assert result.recovered == 1

    @pytest.mark.asyncio
    async def test_recover_exception_in_check(self):
        """Generic exception in check_command -> counted as error."""
        symovo = AsyncMock()
        ss = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(side_effect=RuntimeError("weird error"))
        ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
        ss.clear_transport = AsyncMock()
        recovery = RecoveryService(symovo, ss)
        result = await recovery.recover()
        assert result.errors == 1
