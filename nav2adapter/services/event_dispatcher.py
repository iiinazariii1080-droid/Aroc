"""
Dispatch events from EventBus to MQTT and persistence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from domain.events import AnyEvent
from services.event_bus import EventBus
from services.mqtt_adapter import MqttAdapter
from services.reliability_metrics import reliability_metrics
from services.state_store import state_store

_LOGGER = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self, bus: EventBus, mqtt: Optional[MqttAdapter]):
        self.bus = bus
        self.mqtt = mqtt
        self._task: Optional[asyncio.Task] = None
        self._running = False

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
                    # Use timeout to periodically check _running flag
                    # This prevents hanging on q.get() when stopping
                    event: AnyEvent = await asyncio.wait_for(q.get(), timeout=1.0)
                    reliability_metrics.inc("event_dispatcher.consume.ok")
                    kind = self._kind_from_type(event.type)

                    # Persist last_result for terminal result.* events
                    if event.type.startswith("result."):
                        await state_store.set_last_result(
                            event.command_id,
                            event.model_dump(),
                        )

                    # Publish to MQTT (if connected)
                    if self.mqtt and self.mqtt.is_connected:
                        await self.mqtt.publish_event(kind, event.model_dump())
                        reliability_metrics.inc("event_dispatcher.publish.success")
                    else:
                        reliability_metrics.inc("event_dispatcher.publish.skipped_disconnected")
                except asyncio.TimeoutError:
                    # Timeout is expected - just check _running and continue
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # P1-2 fix: do NOT re-raise — one bad event must not kill the
                    # entire dispatcher loop.  Log, count, and continue.
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

    def _kind_from_type(self, t: str) -> str:
        if t.startswith("ack."):
            return "ack"
        if t.startswith("state."):
            return "state"
        if t.startswith("result."):
            return "result"
        return "unknown"

