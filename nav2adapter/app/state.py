"""Application lifecycle (startup/shutdown).

Key design goals
----------------
- Single-process: this service owns in-memory state and background tasks.
- Explicit ownership: all long-lived services are created once and stored in `app.state.services`.
- Fast startup: expensive/destructive operations (controller cleanup, recovery) can run in background.
- Graceful shutdown: cancel background tasks, stop publishers, close sockets.

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
from services.symovo_service import SymovoAgvClient
from services.state_store import StateStore
from services.event_bus import EventBus
from services.event_dispatcher import EventDispatcher
from services.event_stream_service import EventStreamService
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
        if hasattr(app.state, "_bg_tasks"):
            try:
                app.state._bg_tasks.remove(t)
            except ValueError:
                pass

    task.add_done_callback(_done)
    if not hasattr(app.state, "_bg_tasks"):
        app.state._bg_tasks = []  # type: ignore[attr-defined]
    app.state._bg_tasks.append(task)  # type: ignore[attr-defined]
    return task


async def startup(app: FastAPI) -> None:
    """Initialize services and background tasks."""
    from app.config import log_config_summary
    log_config_summary()

    if int(app_config.settings.uvicorn_workers or 1) != 1:
        raise RuntimeError(
            f"UVICORN_WORKERS={app_config.settings.uvicorn_workers} is not supported. "
            "Set UVICORN_WORKERS=1 (in-memory state must be single-process)."
        )

    # Core dependencies
    symovo_client = SymovoAgvClient()
    state_store = StateStore()
    event_bus = EventBus()
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

    # Command handler (always on — HTTP-only command delivery)
    command_handler = CommandHandler(
        symovo_client=symovo_client,
        event_bus=event_bus,
        state_store=state_store,
    )

    # Event dispatcher (persists terminal result.* events)
    event_dispatcher = EventDispatcher(event_bus, state_store=state_store)
    await event_dispatcher.start()

    # Status publisher (publishes to state_store + event bus)
    status_publisher = StatusPublisher(
        symovo_client=symovo_client,
        bus=event_bus, state_store=state_store,
    )
    await status_publisher.start()

    # Expose safety tracker for /safety/state route
    app.state.safety_tracker = status_publisher.safety_tracker

    # Event stream service (SSE + poll queues)
    event_stream = EventStreamService(event_bus)
    await event_stream.start()

    # Container
    services = AppServices(
        symovo_client=symovo_client,
        command_handler=command_handler,
        status_publisher=status_publisher,
        event_dispatcher=event_dispatcher,
        event_stream=event_stream,
        event_bus=event_bus,
        state_store=state_store,
        bg_tasks=[],
    )
    app.state.services = services

    # Shutdown fallback: used only when AppServices container is unavailable (partial startup failure).
    app.state.status_publisher = status_publisher
    app.state.event_dispatcher = event_dispatcher

    # Destructive startup operations (optional)
    if app_config.settings.symovo_clear_transports_on_startup:
        _LOGGER.warning(
            "SYMOVO_CLEAR_TRANSPORTS_ON_STARTUP=true: clearing ALL transports on controller (destructive)"
        )

        async def _clear_transports() -> None:
            try:
                deleted = await asyncio.wait_for(
                    symovo_client.clear_all_transports(),
                    timeout=app_config.settings.clear_transports_timeout_s,
                )
                _LOGGER.info(
                    "Cleared transports on controller: %s",
                    len(deleted) if isinstance(deleted, list) else deleted,
                )
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Timeout clearing transports on controller at startup (%.0fs). Continuing without blocking.",
                    app_config.settings.clear_transports_timeout_s,
                )
            except Exception as e:
                _LOGGER.warning("Failed to clear transports on controller at startup: %s", e, exc_info=True)

        if app_config.settings.startup_blocking_clear_transports:
            await _clear_transports()
        else:
            services.bg_tasks.append(_spawn_bg_task(app, _clear_transports(), "clear_transports"))

    # State recovery (sync with controller)
    async def _do_recovery() -> None:
        from services.recovery_service import RecoveryService
        try:
            recovery = RecoveryService(symovo_client, state_store)
            await asyncio.wait_for(
                recovery.recover(),
                timeout=app_config.settings.state_recovery_timeout_s,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "State recovery timed out after %.0fs. Controller may be unreachable. Skipping.",
                app_config.settings.state_recovery_timeout_s,
            )
        except Exception as e:
            _LOGGER.warning("State recovery failed: %s", e, exc_info=True)

    if app_config.settings.startup_blocking_recovery:
        await _do_recovery()
    else:
        services.bg_tasks.append(_spawn_bg_task(app, _do_recovery(), "state_recovery"))

    # Start cache eviction task
    from app.cache import ensure_eviction_task
    ensure_eviction_task()

    _LOGGER.info("AE.HUB Navigation Backend initialized successfully")


async def shutdown(app: FastAPI) -> None:
    """Stop background tasks and disconnect resources."""
    from app.cache import cancel_eviction_task
    cancel_eviction_task()

    services: Optional[AppServices] = getattr(app.state, "services", None)

    try:
        if services is not None:
            await services.stop()
        else:
            # Best-effort legacy shutdown.
            if hasattr(app.state, "status_publisher") and app.state.status_publisher:
                await app.state.status_publisher.stop()
            if hasattr(app.state, "event_dispatcher") and app.state.event_dispatcher:
                await app.state.event_dispatcher.stop()
            if hasattr(app.state, "symovo_client") and app.state.symovo_client:
                await app.state.symovo_client.close()
    finally:
        try:
            store = getattr(services, "state_store", None) if services else None
            if store is not None:
                await store.stop_persistence()
                _LOGGER.info("Persistence writer stopped")
        except Exception as e:
            _LOGGER.warning("Error stopping persistence writer: %s", e)

    _LOGGER.info("AE.HUB Navigation Backend shutdown complete")
