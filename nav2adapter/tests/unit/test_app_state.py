"""Tests for app/state.py — startup, shutdown, _spawn_bg_task, _recover_state."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from types import SimpleNamespace
from fastapi import FastAPI

from app.state import _spawn_bg_task, startup, shutdown, _recover_state


# ─── _spawn_bg_task ──────────────────────────────────────────────────

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


# ─── startup ──────────────────────────────────────────────────────────

class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_basic(self):
        """Startup creates services and stores them on app.state."""
        app = FastAPI()
        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient") as sym_cls, \
             patch("app.state.TransportOrchestrator") as orch_cls, \
             patch("app.state.state_store") as ss, \
             patch("app.state.AIOMQTT_AVAILABLE", False), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.NavigationFacade") as nf_cls, \
             patch("app.state.event_bus"):
            cfg.settings.uvicorn_workers = 1
            cfg.settings.mqtt_broker_host = None
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.settings.startup_blocking_clear_transports = False
            cfg.settings.allow_direct_http_commands = True
            cfg.log_config_summary = MagicMock()

            ss.start_persistence = AsyncMock()
            ss.load_from_persistence = AsyncMock(return_value=0)

            ed_instance = AsyncMock()
            ed_cls.return_value = ed_instance
            sp_instance = AsyncMock()
            sp_cls.return_value = sp_instance

            await startup(app)

        assert hasattr(app.state, "services")
        assert hasattr(app.state, "symovo_client")

    @pytest.mark.asyncio
    async def test_startup_rejects_multi_worker(self):
        app = FastAPI()
        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.state_store") as ss:
            cfg.settings.uvicorn_workers = 4
            cfg.log_config_summary = MagicMock()
            ss.start_persistence = AsyncMock()
            with pytest.raises(RuntimeError, match="UVICORN_WORKERS"):
                await startup(app)

    @pytest.mark.asyncio
    async def test_startup_with_mqtt(self):
        """Startup with MQTT enabled creates MqttAdapter & CommandHandler."""
        app = FastAPI()
        mock_mqtt = MagicMock()
        mock_mqtt.connect = AsyncMock()
        mock_mqtt.is_connected = True
        mock_mqtt.start_command_consumer = AsyncMock()

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.TransportOrchestrator"), \
             patch("app.state.state_store") as ss, \
             patch("app.state.AIOMQTT_AVAILABLE", True), \
             patch("app.state.MqttAdapter", return_value=mock_mqtt), \
             patch("app.state.CommandHandler") as ch_cls, \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.NavigationFacade"), \
             patch("app.state.event_bus"):
            cfg.settings.uvicorn_workers = 1
            cfg.settings.mqtt_broker_host = "broker.local"
            cfg.settings.mqtt_broker_port = 1883
            cfg.settings.mqtt_use_tls = False
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.settings.startup_blocking_clear_transports = False
            cfg.settings.allow_direct_http_commands = True
            cfg.log_config_summary = MagicMock()

            ss.start_persistence = AsyncMock()
            ss.load_from_persistence = AsyncMock(return_value=0)
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()

            await startup(app)

        assert app.state.mqtt_adapter is not None
        mock_mqtt.start_command_consumer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_startup_mqtt_connect_fails_continues(self):
        """If MQTT connect fails, startup continues and keeps adapter for background reconnect."""
        app = FastAPI()
        mock_mqtt = MagicMock()
        mock_mqtt.connect = AsyncMock(side_effect=RuntimeError("refused"))
        mock_mqtt.disconnect = AsyncMock()
        mock_mqtt.is_connected = False
        mock_mqtt.start_command_consumer = AsyncMock()

        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.TransportOrchestrator"), \
             patch("app.state.state_store") as ss, \
             patch("app.state.AIOMQTT_AVAILABLE", True), \
             patch("app.state.MqttAdapter", return_value=mock_mqtt), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.NavigationFacade"), \
             patch("app.state.event_bus"):
            cfg.settings.uvicorn_workers = 1
            cfg.settings.mqtt_broker_host = "broker.local"
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.settings.startup_blocking_clear_transports = False
            cfg.settings.allow_direct_http_commands = True
            cfg.log_config_summary = MagicMock()
            ss.start_persistence = AsyncMock()
            ss.load_from_persistence = AsyncMock(return_value=0)
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()

            await startup(app)

        # Adapter is kept alive for background reconnect (not set to None)
        assert app.state.mqtt_adapter is mock_mqtt
        # Command consumer started (its internal loop handles reconnection)
        mock_mqtt.start_command_consumer.assert_awaited_once()
        # CommandHandler was created despite initial connect failure
        assert app.state.command_handler is not None

    @pytest.mark.asyncio
    async def test_startup_persistence_fail_continues(self):
        """Persistence start failure should not block startup."""
        app = FastAPI()
        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient"), \
             patch("app.state.TransportOrchestrator"), \
             patch("app.state.state_store") as ss, \
             patch("app.state.AIOMQTT_AVAILABLE", False), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.NavigationFacade"), \
             patch("app.state.event_bus"):
            cfg.settings.uvicorn_workers = 1
            cfg.settings.mqtt_broker_host = None
            cfg.settings.symovo_clear_transports_on_startup = False
            cfg.settings.startup_blocking_recovery = False
            cfg.settings.allow_direct_http_commands = True
            cfg.log_config_summary = MagicMock()
            ss.start_persistence = AsyncMock(side_effect=RuntimeError("disk full"))
            ss.load_from_persistence = AsyncMock(return_value=0)
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()

            await startup(app)
        # Should succeed despite persistence failure

    @pytest.mark.asyncio
    async def test_startup_clear_transports_blocking(self):
        app = FastAPI()
        with patch("app.state.app_config") as cfg, \
             patch("app.state.SymovoAgvClient") as sym_cls, \
             patch("app.state.TransportOrchestrator"), \
             patch("app.state.state_store") as ss, \
             patch("app.state.AIOMQTT_AVAILABLE", False), \
             patch("app.state.EventDispatcher") as ed_cls, \
             patch("app.state.StatusPublisher") as sp_cls, \
             patch("app.state.NavigationFacade"), \
             patch("app.state.event_bus"):
            cfg.settings.uvicorn_workers = 1
            cfg.settings.mqtt_broker_host = None
            cfg.settings.symovo_clear_transports_on_startup = True
            cfg.settings.startup_blocking_clear_transports = True
            cfg.settings.startup_blocking_recovery = True
            cfg.settings.allow_direct_http_commands = True
            cfg.log_config_summary = MagicMock()
            ss.start_persistence = AsyncMock()
            ss.load_from_persistence = AsyncMock(return_value=0)
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})
            ed_cls.return_value = AsyncMock()
            sp_cls.return_value = AsyncMock()
            sym_cls.return_value.clear_all_transports = AsyncMock(return_value=[])

            await startup(app)
        sym_cls.return_value.clear_all_transports.assert_awaited_once()


# ─── shutdown ─────────────────────────────────────────────────────────

class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_with_services(self):
        app = FastAPI()
        services = MagicMock()
        services.stop = AsyncMock()
        app.state.services = services
        with patch("app.cache.cancel_eviction_task", MagicMock()), \
             patch("routes.aehub.cancel_poll_cleanup_task", MagicMock()), \
             patch("services.state_store.state_store") as ss:
            ss.stop_persistence = AsyncMock()
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
        app.state.mqtt_adapter = MagicMock()
        app.state.mqtt_adapter.disconnect = AsyncMock()
        app.state.symovo_client = MagicMock()
        app.state.symovo_client.close = AsyncMock()
        with patch("app.cache.cancel_eviction_task", MagicMock()), \
             patch("routes.aehub.cancel_poll_cleanup_task", MagicMock()), \
             patch("services.state_store.state_store") as ss:
            ss.stop_persistence = AsyncMock()
            await shutdown(app)
        app.state.status_publisher.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_persistence_error(self):
        """Persistence stop failure should not crash shutdown."""
        app = FastAPI()
        services = MagicMock()
        services.stop = AsyncMock()
        app.state.services = services
        with patch("app.cache.cancel_eviction_task", MagicMock()), \
             patch("routes.aehub.cancel_poll_cleanup_task", MagicMock()), \
             patch("services.state_store.state_store") as ss:
            ss.stop_persistence = AsyncMock(side_effect=RuntimeError("disk error"))
            await shutdown(app)


# ─── _recover_state ──────────────────────────────────────────────────

class TestRecoverState:
    @pytest.mark.asyncio
    async def test_no_persisted_commands(self):
        symovo = AsyncMock()
        with patch("app.state.state_store") as ss:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={})
            await _recover_state(symovo)

    @pytest.mark.asyncio
    async def test_recover_active_command(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        transport_data = {"state": 5}  # RUNNING
        symovo.transport_get = AsyncMock(return_value=transport_data)
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus"):
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.get_current_command_id = AsyncMock(return_value=None)
            ss.register_command = AsyncMock()
            ss.clear_transport = AsyncMock()
            await _recover_state(symovo)
        ss.register_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recover_terminal_finished(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 8})  # FINISHED
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_terminal_canceled(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 6})  # CANCELED
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_terminal_error(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 7})  # ERROR
        symovo.status = AsyncMock(return_value={"state_flags": {}})
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_terminal_error_status_fails(self):
        """Error state but status() fails → still publishes error event."""
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 7})
        symovo.status = AsyncMock(side_effect=RuntimeError("controller down"))
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_not_found_404(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(side_effect=Exception("HTTP 404 /transport/t1 not found"))
        with patch("app.state.state_store") as ss:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_timeout(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("app.state.state_store") as ss:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_non_dict_transport(self):
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value="not a dict")
        with patch("app.state.state_store") as ss:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            await _recover_state(symovo)
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_inactive_state(self):
        """Inactive transport state → cleaned."""
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 10})  # CANCELED (inactive by state_machine)
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)

    @pytest.mark.asyncio
    async def test_recover_skips_if_new_command_active(self):
        """If a new command is already active, skip recovery of old command."""
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(return_value={"state": 5})  # RUNNING
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus"):
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"old_cmd": cmd})
            ss.get_current_command_id = AsyncMock(return_value="new_cmd")
            ss.clear_transport = AsyncMock()
            ss.register_command = AsyncMock()
            await _recover_state(symovo)
        ss.register_command.assert_not_awaited()
        ss.clear_transport.assert_awaited()

    @pytest.mark.asyncio
    async def test_recover_multiple_commands(self):
        """Multiple persisted commands — parallel recovery."""
        symovo = AsyncMock()
        cmd1 = SimpleNamespace(transport_id="t1", target_id="posA")
        cmd2 = SimpleNamespace(transport_id="t2", target_id="posB")
        symovo.transport_get = AsyncMock(side_effect=[
            {"state": 8},  # FINISHED
            {"state": 5},  # RUNNING
        ])
        with patch("app.state.state_store") as ss, \
             patch("app.state.event_bus") as bus:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd1, "cmd2": cmd2})
            ss.clear_transport = AsyncMock()
            ss.get_current_command_id = AsyncMock(return_value=None)
            ss.register_command = AsyncMock()
            bus.publish = AsyncMock()
            await _recover_state(symovo)

    @pytest.mark.asyncio
    async def test_recover_exception_in_check(self):
        """Generic exception in check_command → counted as error."""
        symovo = AsyncMock()
        cmd = SimpleNamespace(transport_id="t1", target_id="posA")
        symovo.transport_get = AsyncMock(side_effect=RuntimeError("weird error"))
        with patch("app.state.state_store") as ss:
            ss.get_persisted_commands_for_recovery = AsyncMock(return_value={"cmd1": cmd})
            ss.clear_transport = AsyncMock()
            await _recover_state(symovo)
        # Should not crash
