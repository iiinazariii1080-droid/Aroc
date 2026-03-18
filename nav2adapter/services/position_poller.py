"""Position polling loop -- long-poll with fallback to periodic GET.

Polls the Symovo controller for robot position updates, publishes
position telemetry, and monitors proximity to the navigation
goal for force-arrival signalling.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Optional

from app.config import settings
from exceptions import DeviceError, DeviceConnectionError
from domain.events import StateProgressEvent, StateType
from domain.models import NavigationStatus, NavigationStatusEnum, Pose2D, PositionStatus
from domain.state_machine import NavigationStateMachine
from services.event_bus import EventBus
from services.force_arrival import ForceArrivalSignal
from services.navigation_progress import update_session_from_current
from services.state_store import StateStore
from services.status_publishing import publish_nav_status
from services.symovo_service import SymovoAgvClient

_LOGGER = logging.getLogger(__name__)


class PositionPoller:
    """Polls the Symovo controller for position updates."""

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

    # -- Helpers -----------------------------------------------------------

    async def _fetch_pose_with_fallback(self) -> dict:
        """Fetch pose snapshot; fall back to status() on expected errors."""
        try:
            pose_data = await asyncio.wait_for(
                self._client.pose_uncached(),
                timeout=settings.symovo_timeout_seconds,
            )
            await self._store.set_last_raw_pose(pose_data if isinstance(pose_data, dict) else {})
            return pose_data
        except asyncio.TimeoutError:
            _LOGGER.warning("pose_uncached() timed out after %ss", settings.symovo_timeout_seconds)
            raise
        except Exception as e:
            is_expected = isinstance(e, DeviceError) and getattr(e, 'http_status', None) in (404, None)
            if is_expected:
                try:
                    _LOGGER.debug("pose() endpoint failed, trying status() as fallback")
                    status_data = await asyncio.wait_for(
                        self._client.status_uncached(),
                        timeout=settings.symovo_timeout_seconds,
                    )
                    if isinstance(status_data, dict):
                        _LOGGER.debug("Successfully retrieved pose from status() endpoint")
                        await self._store.set_last_raw_status(status_data)
                        await self._store.set_last_raw_pose(status_data)
                        return status_data
                except Exception:
                    _LOGGER.debug("status() fallback also failed", exc_info=True)
            raise

    @staticmethod
    def _parse_pose_data(raw: Any) -> Optional[PositionStatus]:
        from services.pose_parser import parse_position_status
        return parse_position_status(raw)

    async def _publish_position_status(self, position: PositionStatus) -> None:
        try:
            await self._store.set_last_position_status(position)
            _LOGGER.debug("Position saved to state_store: %s", position.model_dump())
            await self._update_progress_from_position(position)
        except Exception as e:
            _LOGGER.error("Failed to publish position status: %s", e, exc_info=True)
            raise

    async def _update_progress_from_position(self, position: PositionStatus) -> None:
        """Update sessions using current position and publish status/event progress."""
        active = await self._store.get_active_commands_for_publishing()
        if not active:
            return
        for command_id, t in active.items():
            if NavigationStateMachine.is_terminal_state(t.state):
                self._fa.reset_dwell(command_id)
                continue
            if t.state == NavigationStateMachine.CANCELING:
                self._fa.reset_dwell(command_id)
                continue
            session = await self._store.get_session(command_id)
            if session is None:
                continue
            session = update_session_from_current(session, Pose2D(x=position.x, y=position.y, map_id=None))
            await self._store.upsert_session(session)

            progress = int(session.progress_percent or 0)

            if settings.symovo_force_arrival_on_proximity:
                try:
                    remaining = math.hypot(position.x - session.goal.x, position.y - session.goal.y)
                    if remaining <= settings.symovo_arrival_dist_m:
                        self._fa.start_dwell(command_id)
                        if self._fa.dwell_elapsed(command_id, settings.symovo_arrival_dwell_s):
                            _LOGGER.warning(
                                "Force ARRIVED flag set: command=%s remaining=%.3fm progress=%s state=%s",
                                command_id, remaining, progress, t.state,
                            )
                            self._fa.signal(command_id)
                            return
                    else:
                        self._fa.reset_dwell(command_id)
                except Exception:
                    _LOGGER.debug("Force-arrival proximity check failed", exc_info=True)

            status_enum = NavigationStateMachine.map_symovo_to_aehub(t.state)
            await publish_nav_status(
                NavigationStatus(status=status_enum, goal_id=command_id, progress_percent=progress, error_reason=None),
                self._store,
            )
            await self._bus.publish(
                StateProgressEvent(type=StateType.PROGRESS.value, command_id=command_id, progress_percent=progress, eta_seconds=None)
            )

    # -- Main loop ---------------------------------------------------------

    async def run(self) -> None:
        """Prefer AMR wait_for_changes; fallback to polling pose."""
        while self._running.is_set():
            try:
                await self._run_inner()
            except asyncio.CancelledError:
                _LOGGER.info("Position poller task cancelled")
                return
            except Exception as e:
                _LOGGER.error("Fatal error in position poller, restarting in 5s: %s", e, exc_info=True)
                await asyncio.sleep(5.0)

    async def _run_inner(self) -> None:
        _LOGGER.info("Position poller (long-poll) started")
        rate_hz = max(0.1, min(100.0, settings.position_status_hz))
        interval = 1.0 / rate_hz
        _LOGGER.info("Position update interval: %.2fs (target rate: %s Hz)", interval, rate_hz)
        use_longpoll = True
        consecutive_errors = 0
        max_backoff = 30.0
        last_publish_time = 0.0
        first_publish = True
        since_token: str = "now"
        _longpoll_fallback_time: float = 0.0
        _LONGPOLL_RETRY_INTERVAL: float = 300.0

        while self._running.is_set():
            try:
                now = time.time()
                should_publish = first_publish or (now - last_publish_time) >= interval

                if not use_longpoll and _longpoll_fallback_time > 0 and (now - _longpoll_fallback_time) >= _LONGPOLL_RETRY_INTERVAL:
                    use_longpoll = True
                    since_token = "now"
                    _longpoll_fallback_time = 0.0
                    _LOGGER.info("Retrying AMR long-poll endpoint after %.0fs polling fallback", _LONGPOLL_RETRY_INTERVAL)

                if use_longpoll:
                    try:
                        poll_timeout = min(settings.transport_watch_timeout, interval * 2)
                        resp = await asyncio.wait_for(
                            self._client.amr_wait_for_changes(since=since_token, timeout=poll_timeout),
                            timeout=poll_timeout + 2.0,
                        )
                        consecutive_errors = 0
                        if isinstance(resp, dict) and "result" in resp and resp["result"] is None:
                            since_token = str(int(time.time()))
                            if not should_publish:
                                sleep_time = interval - (time.time() - last_publish_time)
                                if sleep_time > 0:
                                    await asyncio.sleep(min(sleep_time, 0.5))
                                continue
                        else:
                            if isinstance(resp, dict):
                                result = resp.get("result")
                                if isinstance(result, dict):
                                    ts = result.get("timestamp") or result.get("last_update_epoch")
                                    if isinstance(ts, (int, float)) and ts > 0:
                                        since_token = str(int(ts))
                                    elif isinstance(ts, str) and ts.isdigit():
                                        since_token = ts
                                if since_token == "now":
                                    since_token = str(int(time.time()))
                    except asyncio.TimeoutError:
                        if not should_publish:
                            continue
                    except Exception as e:
                        is_endpoint_error = isinstance(e, (DeviceError, DeviceConnectionError)) and (
                            getattr(e, 'http_status', None) in (404, None)
                        )
                        if is_endpoint_error:
                            _LOGGER.warning("AMR wait_for_changes endpoint not available (%s), falling back to polling", e)
                            use_longpoll = False
                            _longpoll_fallback_time = time.time()
                            should_publish = True
                            consecutive_errors = 0
                        else:
                            consecutive_errors += 1
                            if consecutive_errors >= 3:
                                _LOGGER.warning("Long-poll failed %d times, falling back to polling: %s", consecutive_errors, e)
                                use_longpoll = False
                                _longpoll_fallback_time = time.time()
                                should_publish = True
                                consecutive_errors = 0
                            else:
                                backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                                _LOGGER.warning("Long-poll error (attempt %d), backing off %.1fs: %s", consecutive_errors, backoff, e)
                                await asyncio.sleep(backoff)
                                if first_publish or (time.time() - last_publish_time) >= interval:
                                    should_publish = True
                                else:
                                    continue

                if should_publish or not use_longpoll or first_publish:
                    _LOGGER.debug("Fetching pose (should_publish=%s, use_longpoll=%s, first_publish=%s)", should_publish, use_longpoll, first_publish)
                    try:
                        pose_data = await self._fetch_pose_with_fallback()
                        consecutive_errors = 0
                    except Exception as fetch_err:
                        consecutive_errors += 1
                        backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                        error_str = str(fetch_err).lower()
                        is_expected = any(s in error_str for s in ("404", "empty response", "not found"))
                        if is_expected:
                            _LOGGER.debug("Pose endpoint temporarily unavailable (attempt %d): %s", consecutive_errors, error_str[:100])
                        else:
                            _LOGGER.warning("Pose fetch error (attempt %d), backing off %.1fs: %s", consecutive_errors, backoff, fetch_err)
                        await asyncio.sleep(backoff)
                        continue

                    position = self._parse_pose_data(pose_data)
                    if position is not None:
                        await self._publish_position_status(position)
                        last_publish_time = time.time()
                        first_publish = False
                        _LOGGER.debug("Published position: x=%.2f, y=%.2f, theta=%.3f rad", position.x, position.y, position.theta)

                elapsed = time.time() - last_publish_time
                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                _LOGGER.info("Position poller cancelled")
                raise
            except Exception as e:
                consecutive_errors += 1
                backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                _LOGGER.error("Unexpected error in position poller (attempt %d), backing off %.1fs: %s", consecutive_errors, backoff, e, exc_info=True)
                await asyncio.sleep(backoff)
