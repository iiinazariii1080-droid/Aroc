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
from services.transport_orchestrator import TransportOrchestrator
from services.mqtt_adapter import MqttAdapter
from services.command_handler import CommandHandler
from services.status_publisher import StatusPublisher
from services.event_dispatcher import EventDispatcher
from app.navigation_facade import NavigationFacade

_LOGGER = logging.getLogger(__name__)


@dataclass
class AppServices:
    symovo_client: SymovoAgvClient
    transport_orchestrator: TransportOrchestrator
    mqtt_adapter: Optional[MqttAdapter]
    command_handler: Optional[CommandHandler]
    status_publisher: StatusPublisher
    event_dispatcher: EventDispatcher
    navigation_facade: NavigationFacade

    # Background tasks spawned during startup (recovery, destructive cleanup, etc.)
    bg_tasks: list[asyncio.Task]

    async def stop(self) -> None:
        """Best-effort stop of all services owned by the container."""
        # Cancel background tasks first (they often call symovo/mqtt).
        for t in list(self.bg_tasks):
            if not t.done():
                t.cancel()
        for t in list(self.bg_tasks):
            try:
                await t
            except asyncio.CancelledError:
                pass
            except BaseException:
                _LOGGER.debug("Background task failed during shutdown", exc_info=True)

        # Stop publisher/dispatcher.
        try:
            await self.status_publisher.stop()
        except Exception:
            _LOGGER.debug("StatusPublisher stop failed", exc_info=True)

        try:
            await self.event_dispatcher.stop()
        except Exception:
            _LOGGER.debug("EventDispatcher stop failed", exc_info=True)

        # Disconnect MQTT.
        if self.mqtt_adapter is not None:
            try:
                await self.mqtt_adapter.disconnect()
            except Exception:
                _LOGGER.debug("MQTT disconnect failed", exc_info=True)

        # Close Symovo.
        try:
            await self.symovo_client.close()
        except Exception:
            _LOGGER.debug("Symovo client close failed", exc_info=True)
