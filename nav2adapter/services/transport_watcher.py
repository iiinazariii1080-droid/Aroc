"""Per-transport long-poll watcher task.

Watches a single Symovo transport via long-poll, translates controller
state transitions into AE.HUB lifecycle events, and handles force-arrival
when the robot reaches proximity to the goal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Dict, Optional

from app.config import settings
from exceptions import DeviceConnectionError, DeviceError
from domain.events import (
    ResultCanceledEvent,
    ResultErrorEvent,
    ResultSuccessEvent,
    ResultType,
    StateProgressEvent,
    StateType,
)
from domain.models import NavigationSession, NavigationStatus, NavigationStatusEnum
from domain.state_machine import NavigationStateMachine
from services.error_mapper import ErrorMapper
from services.event_bus import EventBus
from services.force_arrival import ForceArrivalSignal
from services.navigation_progress import update_session_from_current
from services.state_store import StateStore
from services.status_publishing import publish_nav_status
from services.symovo_service import SymovoAgvClient

_LOGGER = logging.getLogger(__name__)

# How often we interrupt the long-poll to check for force-arrival.
_FORCE_CHECK_INTERVAL_S: float = 2.0

# Fallback progress percentages when no NavigationSession exists.
_FALLBACK_PROGRESS_RUNNING: int = 50
_FALLBACK_PROGRESS_STARTING: int = 10
_FALLBACK_PROGRESS_OTHER: int = 5

# Exponential backoff parameters for the poll-error retry loop.
_BACKOFF_BASE_S: float = 0.5
_BACKOFF_MAX_S: float = 8.0

# How often to refresh transport state when long-poll returns a heartbeat.
_STATE_REFRESH_INTERVAL_S: float = 3.0


async def check_transport_active(
    state_store: StateStore,
    command_id: str,
    transport_id: str,
    expected_generation: int,
    context: str,
) -> bool:
    """Return True if transport is still active with correct generation."""
    active_transport = await state_store.get_active_transport(command_id)
    if not active_transport or active_transport.transport_id != transport_id:
        _LOGGER.info(
            "Transport %s for command %s is no longer active during %s, stopping watcher",
            transport_id, command_id, context,
        )
        return False
    if active_transport.generation != expected_generation:
        _LOGGER.info(
            "Transport %s for command %s generation changed during %s (%s -> %s), stopping watcher",
            transport_id, command_id, context, expected_generation, active_transport.generation,
        )
        return False
    return True


async def execute_force_arrival(
    command_id: str,
    state_store: StateStore,
    bus: EventBus,
    symovo_client: SymovoAgvClient,
) -> None:
    """Execute the terminal sequence for a force-arrived command."""

    session = await state_store.get_session(command_id)
    if session is not None:
        session.progress_percent = 100
        session.min_remaining_dist_m = 0.0
        await state_store.upsert_session(session)
    await bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id=command_id))
    status = NavigationStatus(
        status=NavigationStatusEnum.ARRIVED,
        goal_id=command_id,
        progress_percent=100,
        error_reason=None,
    )
    await publish_nav_status(status, state_store)
    await state_store.clear_transport(command_id)


async def get_progress_for_command(
    state_store: StateStore,
    command_id: str,
    *,
    fallback_state: int,
) -> int:
    """Prefer session-based progress; fallback to state-based estimate."""
    from domain.models import Pose2D

    if NavigationStateMachine.is_terminal_state(fallback_state):
        session = await state_store.get_session(command_id)
        if session is not None:
            return int(session.progress_percent or 0)
        return 100 if fallback_state == NavigationStateMachine.FINISHED else 0

    session = await state_store.get_session(command_id)
    if session is None:
        if fallback_state in (NavigationStateMachine.RUNNING, NavigationStateMachine.CANCELING):
            return _FALLBACK_PROGRESS_RUNNING
        if fallback_state == NavigationStateMachine.STARTING:
            return _FALLBACK_PROGRESS_STARTING
        return _FALLBACK_PROGRESS_OTHER
    pos = await state_store.get_last_position_status()
    if pos is not None:
        session = update_session_from_current(session, Pose2D(x=pos.x, y=pos.y, map_id=None))
        await state_store.upsert_session(session)
    return int(session.progress_percent or 0)


class TransportWatcherTask:
    """Watches a single transport via long-poll and emits AE.HUB events."""

    def __init__(
        self,
        *,
        symovo_client: SymovoAgvClient,
        bus: EventBus,
        state_store: StateStore,
        force_arrival: ForceArrivalSignal,
        running_flag: asyncio.Event,
    ) -> None:
        self._client = symovo_client
        self._bus = bus
        self._store = state_store
        self._fa = force_arrival
        self._running = running_flag

    async def run(self, *, command_id: str, transport_id: str) -> None:
        """Long-poll a single transport and emit events + status updates."""
        _LOGGER.info("Watching transport %s for command %s", transport_id, command_id)
        progress_heartbeat_interval = 1.0 / max(settings.navigation_status_hz, 0.1)
        last_progress = 0.0
        last_state_refresh = 0.0
        state_refresh_interval_s = _STATE_REFRESH_INTERVAL_S
        consecutive_errors = 0
        max_backoff = _BACKOFF_MAX_S
        base_backoff = _BACKOFF_BASE_S
        since_token: str = "now"

        self._fa.ensure(command_id)

        initial_transport = await self._store.get_active_transport(command_id)
        if not initial_transport or initial_transport.transport_id != transport_id:
            _LOGGER.info("Transport %s for command %s is not active, stopping watcher", transport_id, command_id)
            return
        expected_generation = initial_transport.generation

        def _normalize(payload: Any) -> Dict[str, Any]:
            if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                return payload["result"]
            return payload if isinstance(payload, dict) else {}

        def _update_since(data: Dict[str, Any]) -> None:
            nonlocal since_token
            ts = data.get("timestamp")
            if isinstance(ts, (int, float)) and ts > 0:
                since_token = str(ts)

        # -- Initial state fetch --
        try:
            init_resp = await self._client.transport_get_uncached(transport_id)
            init_data = _normalize(init_resp)
            if isinstance(init_data, dict) and init_data:
                _update_since(init_data)
                init_state = init_data.get("state")
                if isinstance(init_state, int):
                    await self._store.update_transport_state(command_id, init_state)
                last_state_refresh = time.time()
        except Exception as e:
            _LOGGER.debug("Initial transport fetch failed for %s: %s (will rely on long-poll)", transport_id, e)

        while self._running.is_set():
            try:
                if not await check_transport_active(self._store, command_id, transport_id, expected_generation, "pre-poll"):
                    break

                if self._fa.consume(command_id):
                    _LOGGER.info("Force-arrived flag consumed for command %s -- executing terminal sequence", command_id)
                    await execute_force_arrival(command_id, self._store, self._bus, self._client)
                    break

                poll_task: asyncio.Task = asyncio.create_task(
                    self._client.transport_wait_for_changes(
                        transport_id,
                        since=since_token,
                        timeout=settings.transport_watch_timeout,
                    )
                )
                try:
                    while not poll_task.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(poll_task), timeout=_FORCE_CHECK_INTERVAL_S)
                        except asyncio.TimeoutError:
                            pass
                        fa_ev = self._fa.get(command_id)
                        if fa_ev is not None and fa_ev.is_set() and not poll_task.done():
                            poll_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await poll_task
                            _LOGGER.info("Long-poll cancelled early -- force-arrived pending for %s", command_id)
                            break
                    if poll_task.done() and not poll_task.cancelled():
                        resp = poll_task.result()
                    else:
                        continue
                except asyncio.CancelledError:
                    poll_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await poll_task
                    raise
                consecutive_errors = 0

                # Timeout heartbeat
                if isinstance(resp, dict) and "result" in resp and resp["result"] is None:
                    now = time.time()
                    if now - last_state_refresh >= state_refresh_interval_s:
                        try:
                            refreshed = await self._client.transport_get_uncached(transport_id)
                            refreshed_data = _normalize(refreshed)
                            if refreshed_data == {}:
                                await self._store.clear_transport(command_id)
                                break
                            _update_since(refreshed_data)
                            last_state_refresh = now
                            resp = refreshed_data
                        except Exception as e:
                            _LOGGER.debug("Transport refresh failed for %s: %s", transport_id, str(e))

                    if isinstance(resp, dict) and resp.get("result") is None and "result" in resp:
                        if not await check_transport_active(self._store, command_id, transport_id, expected_generation, "heartbeat"):
                            break

                        if now - last_progress >= progress_heartbeat_interval:
                            transport = await self._store.get_active_transport(command_id)
                            if transport:
                                progress = await get_progress_for_command(self._store, command_id, fallback_state=transport.state)
                                status_enum = NavigationStateMachine.map_symovo_to_aehub(transport.state)
                                await publish_nav_status(
                                    NavigationStatus(
                                        status=status_enum, goal_id=command_id,
                                        progress_percent=progress, error_reason=None,
                                    ),
                                    self._store,
                                )
                                await self._bus.publish(
                                    StateProgressEvent(type=StateType.PROGRESS.value, command_id=command_id, progress_percent=progress)
                                )
                            last_progress = now
                        continue

                resp = _normalize(resp)

                if resp == {}:
                    await self._store.clear_transport(command_id)
                    break

                if not await check_transport_active(self._store, command_id, transport_id, expected_generation, "post-poll"):
                    break

                transport_data = resp if isinstance(resp, dict) else {}
                _update_since(transport_data)
                state = transport_data.get("state", 0)
                await self._store.update_transport_state(command_id, state)

                status_enum = NavigationStateMachine.map_symovo_to_aehub(state)
                progress = await get_progress_for_command(self._store, command_id, fallback_state=state)

                error_reason = None
                if status_enum == NavigationStatusEnum.ERROR:
                    try:
                        agv_status = await self._client.status_uncached()
                        state_flags = agv_status.get("state_flags", {}) if isinstance(agv_status, dict) else {}
                        error_reason = ErrorMapper.get_error_reason(transport_data=transport_data, state_flags=state_flags)
                    except Exception as e:
                        _LOGGER.warning("Failed to fetch status for error details: %s", e)
                        error_reason = ErrorMapper.get_error_reason(transport_data=transport_data, state_flags=None)

                await publish_nav_status(
                    NavigationStatus(status=status_enum, goal_id=command_id, progress_percent=progress, error_reason=error_reason),
                    self._store,
                )
                await self._bus.publish(
                    StateProgressEvent(type=StateType.PROGRESS.value, command_id=command_id, progress_percent=progress, eta_seconds=None)
                )

                if NavigationStateMachine.is_terminal_state(state):
                    if state == NavigationStateMachine.FINISHED:
                        await execute_force_arrival(command_id, self._store, self._bus, self._client)
                    elif state == NavigationStateMachine.CANCELED:
                        await self._bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
                        await publish_nav_status(
                            NavigationStatus(status=NavigationStatusEnum.IDLE, goal_id=None, progress_percent=0, error_reason=None),
                            self._store,
                        )
                        await self._store.clear_transport(command_id)
                    else:
                        await self._bus.publish(
                            ResultErrorEvent(type=ResultType.ERROR.value, command_id=command_id, reason=error_reason or "transport_error")
                        )
                        await publish_nav_status(
                            NavigationStatus(
                                status=NavigationStatusEnum.ERROR, goal_id=command_id,
                                progress_percent=0, error_reason=error_reason or "transport_error",
                            ),
                            self._store,
                        )
                        await self._store.clear_transport(command_id)
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                if isinstance(e, DeviceError) and getattr(e, 'http_status', None) == 404:
                    _LOGGER.warning(
                        "Transport %s not found on controller (404). Clearing persisted state for command %s.",
                        transport_id, command_id,
                    )
                    await self._store.clear_transport(command_id)
                    break

                consecutive_errors += 1

                is_conn_error = isinstance(e, (DeviceConnectionError, OSError))
                if is_conn_error and self._fa.is_set(command_id):
                    # Safety guard: only accept force-arrival if cached pose is fresh.
                    pose_age = await self._store.get_last_raw_pose_age_s()
                    if pose_age > settings.force_arrival_max_pose_age_s:
                        _LOGGER.warning(
                            "Controller unreachable + force-arrived pending for %s, "
                            "but cached pose is %.1fs stale (threshold: %.1fs). "
                            "Deferring force-arrival until position is fresh.",
                            command_id, pose_age, settings.force_arrival_max_pose_age_s,
                        )
                    else:
                        self._fa.consume(command_id)
                        _LOGGER.warning(
                            "Controller unreachable + force-arrived pending for %s -- "
                            "reporting error instead of success (robot state unknown). "
                            "Pose age: %.1fs, error: %s",
                            command_id, pose_age, e,
                        )
                        # Safety: do NOT claim arrival when the controller is unreachable.
                        await self._bus.publish(
                            ResultErrorEvent(
                                type=ResultType.ERROR.value,
                                command_id=command_id,
                                reason="controller_unreachable",
                            )
                        )
                        await publish_nav_status(
                            NavigationStatus(
                                status=NavigationStatusEnum.ERROR,
                                goal_id=command_id,
                                progress_percent=0,
                                error_reason="controller_unreachable",
                            ),
                            self._store,
                        )
                        await self._store.clear_transport(command_id)
                        break

                backoff = min(base_backoff * (2 ** min(consecutive_errors - 1, 5)), max_backoff)
                _LOGGER.warning(
                    "Transport watcher error for %s (attempt %d), backing off %.1fs: %s",
                    transport_id, consecutive_errors, backoff, e,
                )
                await asyncio.sleep(backoff)

        self._fa.cleanup(command_id)
