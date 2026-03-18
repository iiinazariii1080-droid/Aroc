"""
Dispatch events from EventBus to persistence.

Subscribes to EventBus and persists terminal result.* events
(result.success, result.error, result.canceled) to state store.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from domain.events import AnyEvent
from services.event_bus import EventBus
from services.reliability_metrics import reliability_metrics
from services.state_store import StateStore

_LOGGER = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self, bus: EventBus, state_store: Optional[StateStore] = None):
        self.bus = bus
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._state_store = state_store

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        q = await self.bus.subscribe()
        try:
            while self._running:
                try:
                    event: AnyEvent = await asyncio.wait_for(q.get(), timeout=1.0)
                    reliability_metrics.inc("event_dispatcher.consume.ok")

                    # Persist last_result for terminal result.* events
                    if event.type.startswith("result.") and self._state_store is not None:
                        payload = event.model_dump()
                        await self._state_store.set_last_result(event.command_id, payload)

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    reliability_metrics.inc("event_dispatcher.consume.failed")
                    _LOGGER.exception("EventDispatcher: failed to process event, skipping")
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            reliability_metrics.inc("event_dispatcher.runtime.failed")
            _LOGGER.error("EventDispatcher failed: %s", e, exc_info=True)
        finally:
            await self.bus.unsubscribe(q)
