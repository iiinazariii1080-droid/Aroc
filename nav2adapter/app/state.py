"""Application lifecycle (startup/shutdown).

Key design goals
----------------
- Single-process: this service owns in-memory state, background tasks, and MQTT consumer.
- Explicit ownership: all long-lived services are created once and stored in `app.state.services`.
- Fast startup: expensive/destructive operations (controller cleanup, recovery) can run in background.
- Graceful shutdown: cancel background tasks, stop publishers, disconnect MQTT, close sockets.

This module intentionally contains only lifecycle orchestration.
Domain logic belongs to services/* and domain/*.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import FastAPI

from app import config as app_config
from app.container import AppServices
from app.navigation_facade import NavigationFacade
from domain.models import NavigationCommand
from domain.events import ResultType, ResultSuccessEvent, ResultCanceledEvent, ResultErrorEvent
from services.symovo_service import SymovoAgvClient
from services.transport_orchestrator import TransportOrchestrator
from services.state_store import state_store
from services.event_bus import event_bus
from services.event_dispatcher import EventDispatcher
from services.error_mapper import ErrorMapper

from services.mqtt_adapter import MqttAdapter, AIOMQTT_AVAILABLE
from services.command_handler import CommandHandler
from services.status_publisher import StatusPublisher

_LOGGER = logging.getLogger(__name__)


def _spawn_bg_task(app: FastAPI, coro, name: str) -> asyncio.Task:
    """Create background task and attach a robust error logger."""
    task = asyncio.create_task(coro, name=name)

    def _done(t: asyncio.Task) -> None:
        try:
            _ = t.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.error("Background task %s failed", name, exc_info=True)
        # Remove finished task from internal list to avoid memory leak
        if hasattr(app.state, "_bg_tasks"):
            try:
                app.state._bg_tasks.remove(t)
            except ValueError:
                pass

    task.add_done_callback(_done)
    # Keep an internal list for cancellation during shutdown.
    if not hasattr(app.state, "_bg_tasks"):
        app.state._bg_tasks = []  # type: ignore[attr-defined]
    app.state._bg_tasks.append(task)  # type: ignore[attr-defined]
    return task


async def startup(app: FastAPI) -> None:
    """Initialize services and background tasks."""
    # Log config now that logging.basicConfig has already run in main.py
    from app.config import log_config_summary
    log_config_summary()

    # Multi-worker is incompatible with in-memory state and MQTT consumption.
    if int(app_config.settings.uvicorn_workers or 1) != 1:
        raise RuntimeError(
            f"UVICORN_WORKERS={app_config.settings.uvicorn_workers} is not supported. "
            "Set UVICORN_WORKERS=1 (in-memory state + MQTT consumer must be single-process)."
        )

    # Core dependencies (always on)
    symovo_client = SymovoAgvClient()
    transport_orchestrator = TransportOrchestrator(symovo_client)

    # Expose stable handles for DI compatibility.
    app.state.symovo_client = symovo_client

    # Persistence writer (non-blocking IO)
    try:
        await state_store.start_persistence()
        _LOGGER.info("Persistence writer started")
    except Exception as e:
        _LOGGER.warning("Failed to start persistence writer: %s", e)

    # Load persisted sessions (idempotency across restarts)
    try:
        loaded = await state_store.load_from_persistence()
        if loaded:
            _LOGGER.info("Loaded %s persisted session(s)", loaded)
    except Exception as e:
        _LOGGER.warning("Failed to load persistence: %s", e)

    # MQTT (optional, but preferred)
    mqtt_adapter: Optional[MqttAdapter] = None
    if app_config.settings.mqtt_broker_host and AIOMQTT_AVAILABLE:
        mqtt_adapter = MqttAdapter()
        try:
            await mqtt_adapter.connect()
            _LOGGER.info(
                "MQTT connected: %s:%s (TLS=%s)",
                app_config.settings.mqtt_broker_host,
                app_config.settings.mqtt_broker_port,
                app_config.settings.mqtt_use_tls,
            )
        except Exception as e:
            _LOGGER.error("Failed to connect MQTT at startup: %s", e, exc_info=True)
            _LOGGER.warning(
                "MQTT will keep retrying in background via command consumer reconnect loop"
            )
            # Do NOT set mqtt_adapter = None — keep it alive so the consumer
            # reconnect loop can establish the connection later.
    elif app_config.settings.mqtt_broker_host and not AIOMQTT_AVAILABLE:
        _LOGGER.warning("aiomqtt not available; running without MQTT")

    # Command handler and consumers — create even if MQTT is not yet connected.
    # The consumer's internal reconnect loop will establish MQTT when the broker
    # becomes reachable, so commands arriving via HTTP can wait for reconnection.
    command_handler: Optional[CommandHandler] = None
    if mqtt_adapter is not None:
        command_handler = CommandHandler(
            symovo_client=symovo_client,
            transport_orchestrator=transport_orchestrator,
            mqtt_adapter=mqtt_adapter,
            event_bus=event_bus,
        )

        async def handle_drive_to_position(payload: dict):
            try:
                command = NavigationCommand(**payload)
                await command_handler.handle_drive_to_position(command)
            except Exception:
                _LOGGER.error("Error handling driveToPosition", exc_info=True)

        async def handle_cancel(payload: dict):
            try:
                command_id = payload.get("command_id")
                if command_id:
                    await command_handler.handle_cancel(command_id)
            except Exception:
                _LOGGER.error("Error handling cancel", exc_info=True)

        await mqtt_adapter.start_command_consumer(handle_drive_to_position, handle_cancel)

    # Event dispatcher (always on: drives SSE and optionally MQTT event topics)
    event_dispatcher = EventDispatcher(event_bus, mqtt_adapter)
    await event_dispatcher.start()

    # Status publisher (always on: publishes to state_store + event bus; MQTT optional)
    status_publisher = StatusPublisher(symovo_client=symovo_client, mqtt_adapter=mqtt_adapter, bus=event_bus)
    await status_publisher.start()

    # Expose safety tracker for /safety/state route
    app.state.safety_tracker = status_publisher.safety_tracker

    # Facade for routes
    navigation_facade = NavigationFacade(
        mqtt_adapter,
        command_handler=command_handler,
        allow_direct_http_commands=app_config.settings.allow_direct_http_commands,
    )

    # Container
    services = AppServices(
        symovo_client=symovo_client,
        transport_orchestrator=transport_orchestrator,
        mqtt_adapter=mqtt_adapter,
        command_handler=command_handler,
        status_publisher=status_publisher,
        event_dispatcher=event_dispatcher,
        navigation_facade=navigation_facade,
        bg_tasks=[],
    )
    app.state.services = services

    # Backward-compatible fields on app.state
    app.state.mqtt_adapter = mqtt_adapter
    app.state.command_handler = command_handler
    app.state.status_publisher = status_publisher
    app.state.event_bus = event_bus
    app.state.event_dispatcher = event_dispatcher
    app.state.navigation_facade = navigation_facade

    # Destructive startup operations (optional)
    if app_config.settings.symovo_clear_transports_on_startup:
        _LOGGER.warning(
            "SYMOVO_CLEAR_TRANSPORTS_ON_STARTUP=true: clearing ALL transports on controller (destructive)"
        )

        async def _clear_transports() -> None:
            try:
                deleted = await asyncio.wait_for(symovo_client.clear_all_transports(), timeout=30.0)
                _LOGGER.info(
                    "Cleared transports on controller: %s",
                    len(deleted) if isinstance(deleted, list) else deleted,
                )
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Timeout clearing transports on controller at startup (30s). Continuing without blocking."
                )
            except Exception as e:
                _LOGGER.warning("Failed to clear transports on controller at startup: %s", e, exc_info=True)

        if app_config.settings.startup_blocking_clear_transports:
            await _clear_transports()
        else:
            services.bg_tasks.append(_spawn_bg_task(app, _clear_transports(), "clear_transports"))

    # State recovery (sync with controller)
    async def _do_recovery() -> None:
        try:
            await asyncio.wait_for(_recover_state(symovo_client), timeout=60.0)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "State recovery timed out after 60s. Controller may be unreachable. Skipping."
            )
        except Exception as e:
            _LOGGER.warning("State recovery failed: %s", e, exc_info=True)

    if app_config.settings.startup_blocking_recovery:
        await _do_recovery()
    else:
        services.bg_tasks.append(_spawn_bg_task(app, _do_recovery(), "state_recovery"))

    _LOGGER.info("AE.HUB Navigation Backend initialized successfully")


async def shutdown(app: FastAPI) -> None:
    """Stop background tasks and disconnect resources."""
    # Cancel orphan background tasks not tracked by AppServices.
    from app.cache import cancel_eviction_task
    cancel_eviction_task()
    try:
        from routes.aehub import cancel_poll_cleanup_task
        cancel_poll_cleanup_task()
    except ImportError:
        pass

    # Container-based shutdown if available.
    services: Optional[AppServices] = getattr(app.state, "services", None)

    # Stop persistence writer last (publishers may queue writes).
    try:
        if services is not None:
            await services.stop()
        else:
            # Best-effort legacy shutdown.
            if hasattr(app.state, "status_publisher") and app.state.status_publisher:
                await app.state.status_publisher.stop()
            if hasattr(app.state, "event_dispatcher") and app.state.event_dispatcher:
                await app.state.event_dispatcher.stop()
            if hasattr(app.state, "mqtt_adapter") and app.state.mqtt_adapter:
                await app.state.mqtt_adapter.disconnect()
            if hasattr(app.state, "symovo_client") and app.state.symovo_client:
                await app.state.symovo_client.close()
    finally:
        try:
            await state_store.stop_persistence()
            _LOGGER.info("Persistence writer stopped")
        except Exception as e:
            _LOGGER.warning("Error stopping persistence writer: %s", e)

    _LOGGER.info("AE.HUB Navigation Backend shutdown complete")


async def _recover_state(symovo_client: SymovoAgvClient) -> None:
    """Recover state after restart by syncing with Symovo.

    Only restores commands that are actually active on the controller.
    """
    from domain.state_machine import NavigationStateMachine

    persisted_commands = await state_store.get_persisted_commands_for_recovery()
    if not persisted_commands:
        _LOGGER.info("No persisted commands to recover")
        return

    command_count = len(persisted_commands)
    _LOGGER.info("Checking %s persisted commands for recovery (loaded from persistence file)...", command_count)

    # Parallel processing with bounded concurrency.
    semaphore = asyncio.Semaphore(5)
    recovered_count = 0
    cleaned_count = 0
    not_found_count = 0
    terminal_count = 0
    inactive_count = 0
    error_count = 0

    async def check_command(command_id: str, cmd) -> tuple[str, str, dict]:
        async with semaphore:
            try:
                transport = await asyncio.wait_for(symovo_client.transport_get(cmd.transport_id), timeout=3.0)
                if not isinstance(transport, dict):
                    await state_store.clear_transport(command_id)
                    return (command_id, "not_found", {})

                state = transport.get("state", 0)

                if NavigationStateMachine.is_terminal_state(state):
                    if state == 8:  # FINISHED
                        await event_bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id=command_id))
                    elif state == 6:  # CANCELED
                        await event_bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
                    else:  # ERROR
                        try:
                            agv_status = await asyncio.wait_for(symovo_client.status(), timeout=2.0)
                            state_flags = agv_status.get("state_flags", {}) if isinstance(agv_status, dict) else {}
                            reason = ErrorMapper.get_error_reason(transport_data=transport, state_flags=state_flags)
                            await event_bus.publish(
                                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command_id, reason=reason)
                            )
                        except Exception:
                            await event_bus.publish(
                                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command_id, reason="Unknown error")
                            )
                    await state_store.clear_transport(command_id)
                    return (command_id, "terminal", {"state": state})

                if NavigationStateMachine.is_active_state(state):
                    # P1-3 fix: if a new driveToPosition was received during startup,
                    # skip recovery of old commands to prevent overwriting
                    # _current_command_id and confusing the StatusPublisher.
                    current = await state_store.get_current_command_id()
                    if current is not None and current != command_id:
                        _LOGGER.info(
                            "Skipping recovery of %s: a new command (%s) is already active",
                            command_id, current,
                        )
                        await state_store.clear_transport(command_id)
                        return (command_id, "skipped_new_active", {"state": state})

                    await state_store.register_command(
                        command_id=command_id,
                        transport_id=cmd.transport_id,
                        state=state,
                        target_id=cmd.target_id,
                    )
                    return (command_id, "recovered", {"state": state})

                await state_store.clear_transport(command_id)
                return (command_id, "inactive", {"state": state})

            except asyncio.TimeoutError:
                await state_store.clear_transport(command_id)
                return (command_id, "timeout", {})
            except Exception as e:
                err_str = str(e).lower()
                if "http 404" in err_str and "/transport/" in err_str:
                    await state_store.clear_transport(command_id)
                    return (command_id, "not_found", {})
                return (command_id, "error", {"error": str(e)})

    results = await asyncio.gather(
        *[check_command(cmd_id, cmd) for cmd_id, cmd in persisted_commands.items()],
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            error_count += 1
            continue

        command_id, result_type, details = result
        if result_type == "recovered":
            recovered_count += 1
            _LOGGER.info("Recovered active command %s (state=%s)", command_id, details.get("state"))
        elif result_type == "terminal":
            cleaned_count += 1
            terminal_count += 1
        elif result_type == "inactive":
            cleaned_count += 1
            inactive_count += 1
        elif result_type in ("not_found", "timeout"):
            cleaned_count += 1
            not_found_count += 1
        elif result_type == "error":
            error_count += 1
            _LOGGER.debug("Recovery check error for command %s: %s", command_id, details.get("error"))

    detail_parts = []
    if recovered_count:
        detail_parts.append(f"{recovered_count} recovered")
    if cleaned_count:
        cleanup_details = []
        if not_found_count:
            cleanup_details.append(f"{not_found_count} not found")
        if terminal_count:
            cleanup_details.append(f"{terminal_count} terminal")
        if inactive_count:
            cleanup_details.append(f"{inactive_count} inactive")
        detail_parts.append(f"{cleaned_count} cleaned ({', '.join(cleanup_details)})")
    if error_count:
        detail_parts.append(f"{error_count} errors")

    if detail_parts:
        _LOGGER.info("State recovery completed: %s", ", ".join(detail_parts))
    else:
        _LOGGER.info("State recovery completed: no commands to process")
