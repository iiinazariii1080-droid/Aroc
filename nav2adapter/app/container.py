"""Service container for FastAPI application.

This is a small step toward a cleaner architecture:
- No global singletons inside app/state.
- A single, explicit object owns lifecycle (start/stop).
- App state gets one stable reference (`app.state.services`).

The goal is operational correctness first (clean shutdown, no task leaks).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from services.symovo_service import SymovoAgvClient
from services.command_handler import CommandHandler
from services.status_publisher import StatusPublisher
from services.event_dispatcher import EventDispatcher
from services.event_stream_service import EventStreamService
from services.event_bus import EventBus
from services.state_store import StateStore
_LOGGER = logging.getLogger(__name__)


@dataclass
class AppServices:
    symovo_client: SymovoAgvClient
    command_handler: CommandHandler
    status_publisher: StatusPublisher
    event_dispatcher: EventDispatcher
    event_stream: EventStreamService
    event_bus: EventBus
    state_store: StateStore

    # Background tasks spawned during startup (recovery, destructive cleanup, etc.)
    bg_tasks: list[asyncio.Task]
    _stopped: bool = False

    async def stop(self) -> None:
        """Best-effort stop of all services owned by the container."""
        if self._stopped:
            _LOGGER.debug("AppServices.stop() called again -- skipping")
            return
        self._stopped = True
        # Cancel background tasks first (they often call symovo).
        for t in list(self.bg_tasks):
            if not t.done():
                t.cancel()
        for t in list(self.bg_tasks):
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.debug("Background task failed during shutdown", exc_info=True)

        # Stop event stream service (cleanup task + poll queues).
        try:
            await self.event_stream.stop()
        except Exception:
            _LOGGER.debug("EventStreamService stop failed", exc_info=True)

        # Stop publisher/dispatcher.
        try:
            await self.status_publisher.stop()
        except Exception:
            _LOGGER.debug("StatusPublisher stop failed", exc_info=True)

        try:
            await self.event_dispatcher.stop()
        except Exception:
            _LOGGER.debug("EventDispatcher stop failed", exc_info=True)

        # Close Symovo.
        try:
            await self.symovo_client.close()
        except Exception:
            _LOGGER.debug("Symovo client close failed", exc_info=True)
