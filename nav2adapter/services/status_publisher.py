"""Navigation status orchestrator.

Coordinates the lifecycle of background tasks:
- TransportWatcherTask (per-transport long-poll + state machine)
- PositionPoller (AMR position long-poll / polling)
- StatusPoller (periodic AGV status cache)
- ForceArrivalSignal (cross-component signalling)
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from app.config import settings
from domain.models import NavigationStatus, NavigationStatusEnum
from services.event_bus import EventBus
from services.force_arrival import ForceArrivalSignal
from services.position_poller import PositionPoller
from services.safety_state_tracker import SafetyStateTracker
from services.state_store import StateStore
from services.status_poller import StatusPoller
from services.symovo_service import SymovoAgvClient
from services.status_publishing import publish_nav_status
from services.transport_watcher import TransportWatcherTask

_LOGGER = logging.getLogger(__name__)


class StatusPublisher:
    """Orchestrates navigation status publishing via background tasks.

    Public interface is unchanged (start / stop / safety_tracker) so that
    callers (``app/state.py``, ``app/container.py``) do not need changes.
    """

    def __init__(
        self,
        symovo_client: SymovoAgvClient,
        bus: EventBus,
        state_store: StateStore,
    ):
        self.symovo_client = symovo_client
        self.bus = bus
        self._state_store = state_store
        self.safety_tracker = SafetyStateTracker()

        # Shared running flag (asyncio.Event so sub-components can check)
        self._running_flag = asyncio.Event()

        # Cross-component signalling
        self._force_arrival = ForceArrivalSignal()

        # Sub-components (created lazily in start())
        self._position_poller: Optional[PositionPoller] = None
        self._status_poller: Optional[StatusPoller] = None

        # Task tracking
        self._tasks: list[asyncio.Task] = []
        self._transport_tasks: Dict[str, tuple[str, asyncio.Task]] = {}

    @property
    def is_running(self) -> bool:
        """Whether the status publisher is actively running."""
        return self._running_flag.is_set()

    @property
    def _running(self) -> bool:
        """Backward-compatible alias."""
        return self.is_running

    async def start(self) -> None:
        """Start background tasks for status publishing."""
        if self._running_flag.is_set():
            _LOGGER.warning("Status publisher already running")
            return

        self._running_flag.set()

        self._position_poller = PositionPoller(
            symovo_client=self.symovo_client,
            bus=self.bus,
            state_store=self._state_store,
            force_arrival=self._force_arrival,
            running_flag=self._running_flag,
        )
        self._status_poller = StatusPoller(
            symovo_client=self.symovo_client,
            state_store=self._state_store,
            safety_tracker=self.safety_tracker,
            running_flag=self._running_flag,
        )

        try:
            self._tasks.append(asyncio.create_task(self._transport_manager()))
            _LOGGER.debug("Transport manager task created")

            self._tasks.append(asyncio.create_task(self._position_poller.run()))
            _LOGGER.debug("Position poller task created")

            self._tasks.append(asyncio.create_task(self._status_poller.run()))
            _LOGGER.debug("Status poller task created")

            _LOGGER.info("Status publisher started with %d background task(s)", len(self._tasks))
        except Exception as e:
            _LOGGER.error("Failed to start status publisher tasks: %s", e, exc_info=True)
            self._running_flag.clear()
            raise

    async def stop(self) -> None:
        """Stop background tasks with graceful shutdown."""
        self._running_flag.clear()

        transport_tasks = [t for (_, t) in self._transport_tasks.values()]
        main_tasks = list(self._tasks)

        for task in transport_tasks + main_tasks:
            task.cancel()

        tasks_to_wait = transport_tasks + main_tasks
        if tasks_to_wait:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_wait, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("Some status publisher tasks did not stop gracefully within timeout")

        self._transport_tasks.clear()
        self._tasks.clear()
        self._force_arrival.clear_all()

        _LOGGER.info("Status publisher stopped")

    # -- Transport manager (spawns per-transport watchers) -----------------

    async def _transport_manager(self) -> None:
        """Spawn per-transport long-poll watchers and maintain idle heartbeat."""
        _LOGGER.info("Transport manager started")
        idle_heartbeat_interval = 1.0 / max(settings.navigation_status_hz, 0.1)
        last_idle = 0.0

        while self._running_flag.is_set():
            try:
                active_commands = await self._state_store.get_active_commands_for_publishing()
                active_command_ids = set(active_commands.keys())

                for command_id, t in active_commands.items():
                    existing = self._transport_tasks.get(command_id)
                    if existing is None:
                        watcher = TransportWatcherTask(
                            symovo_client=self.symovo_client,
                            bus=self.bus,
                            state_store=self._state_store,
                            force_arrival=self._force_arrival,
                            running_flag=self._running_flag,
                        )
                        self._transport_tasks[command_id] = (
                            t.transport_id,
                            asyncio.create_task(watcher.run(command_id=command_id, transport_id=t.transport_id)),
                        )
                    else:
                        existing_transport_id, existing_task = existing
                        if existing_transport_id != t.transport_id or existing_task.done():
                            existing_task.cancel()
                            watcher = TransportWatcherTask(
                                symovo_client=self.symovo_client,
                                bus=self.bus,
                                state_store=self._state_store,
                                force_arrival=self._force_arrival,
                                running_flag=self._running_flag,
                            )
                            self._transport_tasks[command_id] = (
                                t.transport_id,
                                asyncio.create_task(watcher.run(command_id=command_id, transport_id=t.transport_id)),
                            )

                # Stop watchers for commands no longer active
                for command_id in list(self._transport_tasks.keys()):
                    if command_id not in active_command_ids:
                        _, task = self._transport_tasks.pop(command_id, (None, None))
                        if task:
                            task.cancel()

                # Idle heartbeat
                if not active_commands:
                    now = time.time()
                    if now - last_idle >= idle_heartbeat_interval:
                        try:
                            last_status, last_ts = await self._state_store.get_last_navigation_status_with_ts()
                            hold_s = float(getattr(settings, "navigation_terminal_hold_s", 0.0) or 0.0)
                            should_hold = bool(
                                hold_s > 0.0
                                and last_status is not None
                                and getattr(last_status, "status", None) == NavigationStatusEnum.ARRIVED
                                and (now - float(last_ts or 0.0)) <= hold_s
                            )
                            if should_hold:
                                await publish_nav_status(
                                    last_status, self._state_store, update_store=False,
                                )
                            else:
                                await publish_nav_status(
                                    NavigationStatus(
                                        status=NavigationStatusEnum.IDLE,
                                        goal_id=None,
                                        progress_percent=0,
                                        error_reason=None,
                                    ),
                                    self._state_store,
                                )
                        except Exception as e:
                            _LOGGER.warning("Failed to publish idle heartbeat: %s", e)
                        last_idle = now

                await asyncio.sleep(0.1 if active_commands else 1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("Transport manager error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)
