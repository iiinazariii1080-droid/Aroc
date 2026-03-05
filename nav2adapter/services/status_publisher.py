"""
Status publisher for navigation and position status.
"""
import asyncio
import contextlib
import logging
import time
import math
from typing import Optional, Dict, Any
from services.symovo_service import SymovoAgvClient
from services.transport_orchestrator import TransportOrchestrator
from services.mqtt_adapter import MqttAdapter
from services.state_store import state_store
from services.error_mapper import ErrorMapper
from domain.models import NavigationStatus, NavigationStatusEnum, PositionStatus, Pose2D, NavigationSession
from domain.state_machine import NavigationStateMachine
from app.config import settings
from services.event_bus import EventBus, event_bus
from services import charger_workflow
from services.navigation_progress import update_session_from_current
from domain.events import (
    StateProgressEvent,
    ResultSuccessEvent,
    ResultErrorEvent,
    ResultCanceledEvent,
    ResultType,
    StateType,
 )

_LOGGER = logging.getLogger(__name__)


class StatusPublisher:
    """Publishes navigation and position status via MQTT."""
    
    def __init__(
        self,
        symovo_client: SymovoAgvClient,
        mqtt_adapter: Optional[MqttAdapter],
        bus: EventBus = event_bus,
    ):
        self.symovo_client = symovo_client
        self.mqtt_adapter = mqtt_adapter
        self.bus = bus
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._transport_tasks: Dict[str, tuple[str, asyncio.Task]] = {}
        # Track "at goal" dwell per command for force-arrival fallback.
        self._at_goal_since: Dict[str, float] = {}
        # Flag set by position-watcher when force-arrival fires.
        # Only _watch_transport reads+clears this to execute the terminal sequence,
        # eliminating the race where both paths finalise the same command.
        self._force_arrived_commands: set[str] = set()
    
    async def start(self) -> None:
        """Start background tasks for status publishing."""
        if self._running:
            _LOGGER.warning("Status publisher already running")
            return
        
        self._running = True
        
        try:
            # Start transport manager (spawns long-poll watchers per transport)
            nav_task = asyncio.create_task(self._transport_manager())
            self._tasks.append(nav_task)
            _LOGGER.debug("Transport manager task created")
            
            # Start position watcher (prefer AMR long-poll, fallback to polling pose)
            pos_task = asyncio.create_task(self._watch_position_longpoll())
            self._tasks.append(pos_task)
            _LOGGER.debug("Position watcher task created")
            
            # Start background status cache loop (feeds /status route)
            status_task = asyncio.create_task(self._watch_status_loop())
            self._tasks.append(status_task)
            _LOGGER.debug("Status cache watcher task created")
            
            _LOGGER.info("Status publisher started with %d background task(s)", len(self._tasks))
        except Exception as e:
            _LOGGER.error("Failed to start status publisher tasks: %s", e, exc_info=True)
            self._running = False
            raise
    
    async def stop(self) -> None:
        """Stop background tasks with graceful shutdown."""

        self._running = False

        # Snapshot tasks to avoid losing references before awaiting
        transport_tasks = [t for (_, t) in self._transport_tasks.values()]
        main_tasks = list(self._tasks)

        # Cancel all background tasks
        for task in transport_tasks:
            task.cancel()
        for task in main_tasks:
            task.cancel()

        # Wait for everything to stop (including transport watchers)
        tasks_to_wait = transport_tasks + main_tasks
        if tasks_to_wait:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_wait, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("Some status publisher tasks did not stop gracefully within timeout")

        # Cleanup internal registries
        self._transport_tasks.clear()
        self._tasks.clear()
        self._at_goal_since.clear()
        self._force_arrived_commands.clear()

        _LOGGER.info("Status publisher stopped")
    
    async def _transport_manager(self) -> None:
        """Spawn per-transport long-poll watchers and maintain idle heartbeat."""
        _LOGGER.info("Transport manager started")

        idle_heartbeat_interval = 1.0 / max(settings.navigation_status_hz, 0.1)
        last_idle = 0.0

        while self._running:
            try:
                # Publish/watch only the current command (single-active policy).
                active_commands = await state_store.get_active_commands_for_publishing()

                # Start/refresh watchers per command_id (avoid transport_id key collisions)
                active_command_ids = set(active_commands.keys())

                for command_id, t in active_commands.items():
                    existing = self._transport_tasks.get(command_id)
                    if existing is None:
                        self._transport_tasks[command_id] = (
                            t.transport_id,
                            asyncio.create_task(self._watch_transport(command_id=command_id, transport_id=t.transport_id)),
                        )
                    else:
                        existing_transport_id, existing_task = existing
                        # If transport_id changed or previous task died, restart watcher
                        if existing_transport_id != t.transport_id or existing_task.done():
                            existing_task.cancel()
                            self._transport_tasks[command_id] = (
                                t.transport_id,
                                asyncio.create_task(self._watch_transport(command_id=command_id, transport_id=t.transport_id)),
                            )

                # Stop watchers for commands no longer active
                for command_id in list(self._transport_tasks.keys()):
                    if command_id not in active_command_ids:
                        _, task = self._transport_tasks.pop(command_id, (None, None))
                        if task:
                            task.cancel()

                # If no active commands, publish idle heartbeat.
                # BUT: keep ARRIVED visible for a short time to avoid arrived->idle flicker.
                if not active_commands:
                    now = time.time()
                    if now - last_idle >= idle_heartbeat_interval:
                        try:
                            last_status, last_ts = await state_store.get_last_navigation_status_with_ts()
                            hold_s = float(getattr(settings, "navigation_terminal_hold_s", 0.0) or 0.0)
                            should_hold = bool(
                                hold_s > 0.0
                                and last_status is not None
                                and getattr(last_status, "status", None) == NavigationStatusEnum.ARRIVED
                                and (now - float(last_ts or 0.0)) <= hold_s
                            )
                            if should_hold:
                                # Publish to MQTT but DO NOT overwrite state_store timestamps/status,
                                # so hold window is based on first ARRIVED time.
                                await self._publish_navigation_status(last_status, update_store=False)
                            else:
                                await self._publish_navigation_status(
                                    NavigationStatus(
                                        status=NavigationStatusEnum.IDLE,
                                        goal_id=None,
                                        progress_percent=0,
                                        error_reason=None,
                                    )
                                )
                        except Exception as e:
                            _LOGGER.warning("Failed to publish idle heartbeat: %s", e)
                        last_idle = now

                # Adaptive sleep: poll fast when commands are active, slow when idle.
                await asyncio.sleep(0.1 if active_commands else 1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("Transport manager error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _watch_transport(self, *, command_id: str, transport_id: str) -> None:
        """Long-poll a single transport and emit AE.HUB events + status updates."""
        _LOGGER.info("Watching transport %s for command %s", transport_id, command_id)
        progress_heartbeat_interval = 1.0 / max(settings.navigation_status_hz, 0.1)
        last_progress = 0.0
        # If long-poll never returns a terminal state, we still need a periodic refresh.
        last_state_refresh = 0.0
        state_refresh_interval_s = 3.0
        consecutive_errors = 0
        max_backoff = 8.0
        base_backoff = 0.5
        # IMPORTANT: do NOT call long-poll with since="now" repeatedly.
        # If a change happens between requests (e.g. FINISHED), we'd miss it forever.
        # Use the transport object's `timestamp` (Unix ts) as cursor for the next call.
        since_token: str = "now"
        
        # Capture generation token at start to prevent stale publications
        initial_transport = await state_store.get_active_transport(command_id)
        if not initial_transport or initial_transport.transport_id != transport_id:
            _LOGGER.info("Transport %s for command %s is not active, stopping watcher", transport_id, command_id)
            return
        expected_generation = initial_transport.generation

        def _normalize_transport_payload(payload: Any) -> Dict[str, Any]:
            if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                return payload["result"]
            return payload if isinstance(payload, dict) else {}
        
        def _maybe_update_since_from_transport(data: Dict[str, Any]) -> None:
            nonlocal since_token
            ts = data.get("timestamp")
            if isinstance(ts, (int, float)) and ts > 0:
                # Symovo expects string cursor
                since_token = str(ts)

        # ── Initial state fetch ──────────────────────────────────────
        # Seed current state + since_token BEFORE entering the long-poll loop.
        # Without this, a transport that transitions (e.g. → ERROR) between
        # transport_start() and the watcher's first poll (since="now") would
        # go unnoticed for up to transport_watch_timeout (30 s).
        try:
            init_resp = await self.symovo_client.transport_get_uncached(transport_id)
            init_data = _normalize_transport_payload(init_resp)
            if isinstance(init_data, dict) and init_data:
                _maybe_update_since_from_transport(init_data)
                init_state = init_data.get("state")
                if isinstance(init_state, int):
                    await state_store.update_transport_state(command_id, init_state)
                    if NavigationStateMachine.is_terminal_state(init_state):
                        _LOGGER.info(
                            "Transport %s already terminal (state=%s) at watcher start",
                            transport_id, init_state,
                        )
                        # Fall through to the main loop which will process terminal on first iteration.
                last_state_refresh = time.time()
        except Exception as e:
            _LOGGER.debug("Initial transport fetch failed for %s: %s (will rely on long-poll)", transport_id, e)

        while self._running:
            try:
                # CRITICAL: Check if transport is still active before each long-poll.
                # This prevents hanging on long-poll requests after cancel.
                # Also check generation token to prevent stale publications.
                active_transport = await state_store.get_active_transport(command_id)
                if not active_transport or active_transport.transport_id != transport_id:
                    _LOGGER.info("Transport %s for command %s is no longer active, stopping watcher", transport_id, command_id)
                    break
                if active_transport.generation != expected_generation:
                    _LOGGER.info("Transport %s for command %s generation changed (%s -> %s), stopping watcher", transport_id, command_id, expected_generation, active_transport.generation)
                    break

                # P0-1 fix: Check if position-watcher flagged force-arrival.
                # Only _watch_transport executes the terminal sequence (single owner).
                if command_id in self._force_arrived_commands:
                    self._force_arrived_commands.discard(command_id)
                    _LOGGER.info("Force-arrived flag consumed for command %s — executing terminal sequence", command_id)
                    await self._set_session_terminal(command_id, progress_percent=100)
                    session = await state_store.get_session(command_id)
                    if session is not None:
                        await charger_workflow.maybe_activate_after_arrival(self.symovo_client, session)
                    await self.bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id=command_id))
                    await self._publish_navigation_status(
                        NavigationStatus(
                            status=NavigationStatusEnum.ARRIVED,
                            goal_id=command_id,
                            progress_percent=100,
                            error_reason=None,
                        )
                    )
                    await state_store.clear_transport(command_id)
                    break

                # Run the long-poll as a cancellable task so we can
                # interrupt it every _FORCE_CHECK_INTERVAL_S to consume
                # a force-arrived flag without waiting for the full
                # op_timeout (was 35 s × 4 retries = 147 s block).
                _FORCE_CHECK_INTERVAL_S = 2.0
                poll_task: asyncio.Task = asyncio.create_task(
                    self.symovo_client.transport_wait_for_changes(
                        transport_id,
                        since=since_token,
                        timeout=settings.transport_watch_timeout,
                    )
                )
                try:
                    while not poll_task.done():
                        # Wait in short intervals, checking force-arrived between waits.
                        try:
                            await asyncio.wait_for(asyncio.shield(poll_task), timeout=_FORCE_CHECK_INTERVAL_S)
                        except asyncio.TimeoutError:
                            pass  # poll still running — check flag below
                        if command_id in self._force_arrived_commands and not poll_task.done():
                            poll_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await poll_task
                            _LOGGER.info(
                                "Long-poll cancelled early — force-arrived pending for %s",
                                command_id,
                            )
                            break  # re-enter outer while → force-arrived consumed at top
                    # Re-raise if the task finished with an exception.
                    if poll_task.done() and not poll_task.cancelled():
                        resp = poll_task.result()
                    else:
                        continue  # force-arrived break → restart loop
                except asyncio.CancelledError:
                    poll_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await poll_task
                    raise
                consecutive_errors = 0  # Reset on success

                # Symovo long-poll semantics:
                # - timeout -> None (we get {"result": None})
                # - deleted -> {}
                if isinstance(resp, dict) and "result" in resp and resp["result"] is None:
                    # timeout heartbeat
                    now = time.time()
                    # Periodic state refresh (uncached) so we can observe terminal transitions.
                    if now - last_state_refresh >= state_refresh_interval_s:
                        try:
                            refreshed = await self.symovo_client.transport_get_uncached(transport_id)
                            refreshed_data = _normalize_transport_payload(refreshed)
                            if refreshed_data == {}:
                                await state_store.clear_transport(command_id)
                                break
                            _maybe_update_since_from_transport(refreshed_data)
                            resp = refreshed_data
                            last_state_refresh = now
                            # fall through to normal handling below
                        except Exception as e:
                            _LOGGER.debug("Transport refresh failed for %s: %s", transport_id, str(e))
                            # continue with heartbeat publish below

                    # If refresh didn't replace resp, keep heartbeat behavior
                    if not isinstance(resp, dict):
                        resp = {"result": None}

                    # If we still have timeout payload, do heartbeat + optional force-arrival
                    if isinstance(resp, dict) and "result" in resp and resp["result"] is None:
                        # Check if transport is still active before publishing heartbeat
                        # Also check generation token to prevent stale publications
                        active_transport = await state_store.get_active_transport(command_id)
                        if not active_transport or active_transport.transport_id != transport_id:
                            _LOGGER.info("Transport %s for command %s is no longer active during heartbeat, stopping watcher", transport_id, command_id)
                            break
                        if active_transport.generation != expected_generation:
                            _LOGGER.info("Transport %s for command %s generation changed during heartbeat (%s -> %s), stopping watcher", transport_id, command_id, expected_generation, active_transport.generation)
                            break
                        
                        if now - last_progress >= progress_heartbeat_interval:
                            # emit progress heartbeat based on last known state
                            transport = await state_store.get_active_transport(command_id)
                            if transport:
                                # Also publish status heartbeat so HTTP polling UI sees progress.
                                progress = await self._get_progress_for_command(command_id, fallback_state=transport.state)
                                status_enum = TransportOrchestrator.map_symovo_to_aehub(transport.state)
                                await self._publish_navigation_status(
                                    NavigationStatus(
                                        status=status_enum,
                                        goal_id=command_id,
                                        progress_percent=progress,
                                        error_reason=None,
                                    )
                                )
                                await self.bus.publish(
                                    StateProgressEvent(
                                        type=StateType.PROGRESS.value,
                                        command_id=command_id,
                                        progress_percent=progress,
                                    )
                                )
                            last_progress = now
                        continue

                resp = _normalize_transport_payload(resp)

                if resp == {}:
                    # deleted
                    await state_store.clear_transport(command_id)
                    break

                # CRITICAL: Check again if transport is still active after long-poll
                # This handles the case where transport was cancelled during the long-poll
                # Also check generation token to prevent stale publications
                active_transport = await state_store.get_active_transport(command_id)
                if not active_transport or active_transport.transport_id != transport_id:
                    _LOGGER.info("Transport %s for command %s was cancelled during long-poll, stopping watcher", transport_id, command_id)
                    break
                if active_transport.generation != expected_generation:
                    _LOGGER.info("Transport %s for command %s generation changed during long-poll (%s -> %s), stopping watcher", transport_id, command_id, expected_generation, active_transport.generation)
                    break

                transport_data = resp if isinstance(resp, dict) else {}
                _maybe_update_since_from_transport(transport_data)
                state = transport_data.get("state", 0)
                await state_store.update_transport_state(command_id, state)

                status_enum = TransportOrchestrator.map_symovo_to_aehub(state)
                progress = await self._get_progress_for_command(command_id, fallback_state=state)

                error_reason = None
                if status_enum == NavigationStatusEnum.ERROR:
                    # Best-effort: fetch status for error details, but don't fail if it errors
                    # Use uncached for background loops to see current state
                    try:
                        agv_status = await self.symovo_client.status_uncached()
                        state_flags = agv_status.get("state_flags", {}) if isinstance(agv_status, dict) else {}
                        error_reason = ErrorMapper.get_error_reason(transport_data=transport_data, state_flags=state_flags)
                    except Exception as e:
                        _LOGGER.warning("Failed to fetch status for error details: %s", e)
                        error_reason = ErrorMapper.get_error_reason(transport_data=transport_data, state_flags=None)

                await self._publish_navigation_status(
                    NavigationStatus(
                        status=status_enum,
                        goal_id=command_id,
                        progress_percent=progress,
                        error_reason=error_reason,
                    )
                )

                # Emit state.progress
                await self.bus.publish(
                    StateProgressEvent(
                        type=StateType.PROGRESS.value,
                        command_id=command_id,
                        progress_percent=progress,
                        eta_seconds=None,
                    )
                )

                if NavigationStateMachine.is_terminal_state(state):
                    # Terminal: force 100% for FINISHED, keep last for others.
                    if state == NavigationStateMachine.FINISHED:
                        await self._set_session_terminal(command_id, progress_percent=100)
                        session = await state_store.get_session(command_id)
                        if session is not None:
                            await charger_workflow.maybe_activate_after_arrival(self.symovo_client, session)
                        await self.bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id=command_id))
                        # Publish ARRIVED status, then clear transport
                        await self._publish_navigation_status(
                            NavigationStatus(
                                status=NavigationStatusEnum.ARRIVED,
                                goal_id=command_id,
                                progress_percent=100,
                                error_reason=None,
                            )
                        )
                    elif state == NavigationStateMachine.CANCELED:
                        await self.bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
                        # Publish IDLE with goal_id=None for CANCELED (contract requirement)
                        await self._publish_navigation_status(
                            NavigationStatus(
                                status=NavigationStatusEnum.IDLE,
                                goal_id=None,
                                progress_percent=0,
                                error_reason=None,
                            )
                        )
                    else:
                        # ERROR state
                        await self.bus.publish(
                            ResultErrorEvent(
                                type=ResultType.ERROR.value,
                                command_id=command_id,
                                reason=error_reason or "transport_error",
                            )
                        )
                        # Publish ERROR status with goal_id, then clear transport
                        await self._publish_navigation_status(
                            NavigationStatus(
                                status=NavigationStatusEnum.ERROR,
                                goal_id=command_id,
                                progress_percent=0,
                                error_reason=error_reason or "transport_error",
                            )
                        )

                    await state_store.clear_transport(command_id)
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                # If transport no longer exists on controller, clear it to avoid infinite retries after restarts.
                err = str(e).lower()
                if "http 404" in err and "/transport/" in err:
                    _LOGGER.warning(
                        "Transport %s not found on controller (404). Clearing persisted state for command %s.",
                        transport_id,
                        command_id,
                    )
                    await state_store.clear_transport(command_id)
                    break

                consecutive_errors += 1

                # P0-3 fix: On connection-level errors (host unreachable, refused, DNS),
                # immediately consume force-arrived flag instead of retrying the dead controller.
                is_conn_error = any(phrase in err for phrase in (
                    "no route to host", "connection refused", "connect call failed",
                    "name or service not known", "network is unreachable",
                ))
                if is_conn_error and command_id in self._force_arrived_commands:
                    self._force_arrived_commands.discard(command_id)
                    _LOGGER.info(
                        "Controller unreachable + force-arrived pending — consuming immediately "
                        "for command %s (error: %s)", command_id, e,
                    )
                    await self._set_session_terminal(command_id, progress_percent=100)
                    session = await state_store.get_session(command_id)
                    if session is not None:
                        await charger_workflow.maybe_activate_after_arrival(self.symovo_client, session)
                    await self.bus.publish(ResultSuccessEvent(type=ResultType.SUCCESS.value, command_id=command_id))
                    await self._publish_navigation_status(
                        NavigationStatus(
                            status=NavigationStatusEnum.ARRIVED,
                            goal_id=command_id,
                            progress_percent=100,
                            error_reason=None,
                        )
                    )
                    await state_store.clear_transport(command_id)
                    break

                backoff = min(base_backoff * (2 ** min(consecutive_errors - 1, 5)), max_backoff)
                _LOGGER.warning(
                    "Transport watcher error for %s (attempt %d), backing off %.1fs: %s",
                    transport_id, consecutive_errors, backoff, e,
                )
                await asyncio.sleep(backoff)

        # Cleanup tracking state for this command regardless of exit reason
        # (cancel, terminal state, error, generation mismatch, etc.)
        self._at_goal_since.pop(command_id, None)
        self._force_arrived_commands.discard(command_id)

    # ── Helpers extracted from _watch_position_longpoll ──────────────

    async def _fetch_pose_with_fallback(self) -> dict:
        """Fetch pose snapshot from controller; fall back to status() on expected errors.

        Returns raw dict.  Raises on unrecoverable failure.
        Caches raw data in state_store as a side-effect.
        """
        try:
            pose_data = await asyncio.wait_for(
                self.symovo_client.pose_uncached(),
                timeout=settings.symovo_timeout_seconds,
            )
            await state_store.set_last_raw_pose(pose_data if isinstance(pose_data, dict) else {})
            return pose_data
        except asyncio.TimeoutError:
            _LOGGER.warning("pose_uncached() timed out after %ss", settings.symovo_timeout_seconds)
            raise
        except Exception as e:
            error_str = str(e).lower()
            is_expected = any(s in error_str for s in ("404", "empty response", "not found"))
            if is_expected:
                try:
                    _LOGGER.debug("pose() endpoint failed, trying status() as fallback")
                    status_data = await asyncio.wait_for(
                        self.symovo_client.status_uncached(),
                        timeout=settings.symovo_timeout_seconds,
                    )
                    if isinstance(status_data, dict):
                        _LOGGER.debug("Successfully retrieved pose from status() endpoint")
                        await state_store.set_last_raw_status(status_data)
                        await state_store.set_last_raw_pose(status_data)
                        return status_data
                except Exception:
                    _LOGGER.debug("status() fallback also failed", exc_info=True)
            raise

    @staticmethod
    def _parse_pose_data(raw: Any) -> Optional[PositionStatus]:
        """Extract *PositionStatus* from a Symovo pose / status response.

        Handles multiple response formats returned by different firmware versions:

        1. ``{"pose": {"x": …, "y": …, "theta": …}}``
        2. ``{"x": …, "y": …, "theta": …}``  (direct)
        3. ``{"result": {"pose": {…}}}``        (wrapped)
        4. ``{"pose": {"x_m": …, "y_m": …, "theta_deg": …}}``  (normalized)

        Returns ``None`` when the payload cannot be parsed into a valid position.
        """
        # Unwrap list (Symovo sometimes returns [{}])
        if isinstance(raw, list):
            if not raw:
                _LOGGER.warning("Pose data is empty list")
                return None
            raw = raw[0]

        if not isinstance(raw, dict):
            _LOGGER.warning("Pose data is not a dict: %s, value: %s", type(raw), raw)
            return None

        # ── Locate the inner pose dict ──
        pose: Optional[dict] = None
        if "pose" in raw:
            pose = raw.get("pose")
        elif "result" in raw and isinstance(raw.get("result"), dict):
            pose = raw["result"].get("pose")
        else:
            if any(k in raw for k in ("x", "y", "theta")):
                pose = raw

        # Normalized SymovoStatusResponse format (x_m / y_m / theta_deg)
        if (not isinstance(pose, dict) or not pose) and isinstance(raw.get("pose"), dict):
            norm_pose = raw["pose"]
            if "x_m" in norm_pose or "y_m" in norm_pose:
                theta = norm_pose.get("theta_deg")
                if theta is None:
                    theta = norm_pose.get("theta")
                if norm_pose.get("theta_deg") is not None:
                    theta = math.radians(float(norm_pose["theta_deg"]))
                # Use 'is None' to avoid falsy-zero bug (0.0 is a valid coordinate)
                nx = norm_pose.get("x_m")
                if nx is None:
                    nx = norm_pose.get("x")
                ny = norm_pose.get("y_m")
                if ny is None:
                    ny = norm_pose.get("y")
                pose = {
                    "x": nx,
                    "y": ny,
                    "theta": theta,
                }

        if not isinstance(pose, dict):
            _LOGGER.warning("Could not extract pose from data: %s", raw)
            return None

        # ── Extract x / y / theta with format normalization ──
        # Use 'is None' checks to avoid falsy-zero bug (0.0 is a valid coordinate)
        x_val = pose.get("x")
        if x_val is None:
            x_val = pose.get("x_m")
        y_val = pose.get("y")
        if y_val is None:
            y_val = pose.get("y_m")
        theta_val = pose.get("theta")
        if theta_val is None:
            theta_deg = pose.get("theta_deg")
            if theta_deg is not None:
                theta_val = math.radians(float(theta_deg))

        if x_val is None or y_val is None or theta_val is None:
            _LOGGER.warning("Pose data incomplete: x=%s, y=%s, theta=%s, raw=%s", x_val, y_val, theta_val, raw)
            return None

        return PositionStatus(x=float(x_val), y=float(y_val), theta=float(theta_val), frame_id="map")

    # ── Main position-watcher loop ───────────────────────────────────

    async def _watch_position_longpoll(self) -> None:
        """Prefer AMR wait_for_changes; fallback to polling pose.

        P2-6: wrapped in an outer restart-loop so a single fatal error
        does not permanently kill position publishing.
        """
        while self._running:
            try:
                _LOGGER.info("Position watcher (long-poll) started")
                rate_hz = float(settings.position_status_hz) if settings.position_status_hz else 2.0
                rate_hz = max(0.1, min(100.0, rate_hz))
                interval = 1.0 / rate_hz
                _LOGGER.info("Position update interval: %.2fs (target rate: %s Hz, from env: %s)", interval, rate_hz, settings.position_status_hz)
                use_longpoll = True
                consecutive_errors = 0
                max_backoff = 30.0
                last_publish_time = 0.0
                first_publish = True
                since_token: str = "now"
                _longpoll_fallback_time: float = 0.0
                _LONGPOLL_RETRY_INTERVAL: float = 300.0  # retry long-poll every 5 min

                while self._running:
                    try:
                        now = time.time()
                        should_publish = first_publish or (now - last_publish_time) >= interval

                        # Periodically retry long-poll after transient fallback
                        if not use_longpoll and _longpoll_fallback_time > 0 and (now - _longpoll_fallback_time) >= _LONGPOLL_RETRY_INTERVAL:
                            use_longpoll = True
                            since_token = "now"
                            _longpoll_fallback_time = 0.0
                            _LOGGER.info("Retrying AMR long-poll endpoint after %.0fs polling fallback", _LONGPOLL_RETRY_INTERVAL)

                        # ── Long-poll branch ──
                        if use_longpoll:
                            try:
                                poll_timeout = min(settings.transport_watch_timeout, interval * 2)
                                resp = await asyncio.wait_for(
                                    self.symovo_client.amr_wait_for_changes(since=since_token, timeout=poll_timeout),
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
                                from exceptions import DeviceError, DeviceConnectionError
                                error_str = str(e).lower()
                                is_endpoint_error = isinstance(e, (DeviceError, DeviceConnectionError)) and (
                                    "404" in error_str or "empty response" in error_str or "not found" in error_str
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

                        # ── Fetch + parse + publish ──
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

                        # Sleep until next interval
                        elapsed = time.time() - last_publish_time
                        sleep_time = max(0, interval - elapsed)
                        if sleep_time > 0:
                            await asyncio.sleep(sleep_time)
                    except asyncio.CancelledError:
                        _LOGGER.info("Position watcher cancelled")
                        raise  # propagate to outer handler
                    except Exception as e:
                        consecutive_errors += 1
                        backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                        _LOGGER.error("Unexpected error in position watcher (attempt %d), backing off %.1fs: %s", consecutive_errors, backoff, e, exc_info=True)
                        await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                _LOGGER.info("Position watcher task cancelled")
                return
            except Exception as e:
                # P2-6 fix: restart the inner loop with backoff.
                _LOGGER.error("Fatal error in position watcher, restarting in 5s: %s", e, exc_info=True)
                await asyncio.sleep(5.0)

    async def _watch_status_loop(self) -> None:
        """Background loop that periodically fetches full AGV status and caches it in state_store.

        This feeds the ``/status`` HTTP route so it can respond from cache
        instead of hitting the Symovo controller on every request.

        P2-6: wrapped in an outer restart-loop so a single fatal error
        does not permanently kill status caching.
        """
        while self._running:
            rate_hz = float(settings.status_cache_hz) if settings.status_cache_hz else 0.5
            rate_hz = max(0.1, min(10.0, rate_hz))
            interval = 1.0 / rate_hz
            consecutive_errors = 0
            max_backoff = 30.0

            _LOGGER.info(
                "Status cache watcher started: interval=%.2fs (%.1f Hz)",
                interval,
                rate_hz,
            )

            try:
                while self._running:
                    try:
                        raw_status = await self.symovo_client.status_uncached()
                        if isinstance(raw_status, dict):
                            await state_store.set_last_raw_status(raw_status)
                            _LOGGER.debug("Cached raw status from controller")
                            consecutive_errors = 0
                        else:
                            _LOGGER.warning("status_uncached() returned non-dict: %s", type(raw_status))
                    except asyncio.CancelledError:
                        raise  # propagate to outer handler
                    except Exception as e:
                        consecutive_errors += 1
                        error_str = str(e).lower()
                        is_expected = "404" in error_str or "empty response" in error_str or "not found" in error_str
                        if is_expected:
                            _LOGGER.debug(
                                "Status endpoint temporarily unavailable (attempt %d): %s",
                                consecutive_errors,
                                error_str[:120],
                            )
                        else:
                            backoff = min(interval * (2 ** min(consecutive_errors, 5)), max_backoff)
                            _LOGGER.warning(
                                "Status fetch error (attempt %d), backing off %.1fs: %s",
                                consecutive_errors,
                                backoff,
                                e,
                            )
                            await asyncio.sleep(backoff)
                            continue

                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                _LOGGER.info("Status cache watcher cancelled")
                return
            except Exception as e:
                # P2-6 fix: log and restart inner loop instead of dying permanently.
                _LOGGER.error("Fatal error in status cache watcher, restarting in 5s: %s", e, exc_info=True)
                await asyncio.sleep(5.0)

    def _calculate_progress(self, state: int, transport_data: Dict[str, Any]) -> int:
        """
        Calculate progress percentage from transport state.
        
        Args:
            state: Transport state
            transport_data: Transport data
            
        Returns:
            Progress percentage (0-100)
        """
        if state == NavigationStateMachine.FINISHED:
            return 100
        elif state == NavigationStateMachine.ERROR:
            return 0
        elif state == NavigationStateMachine.CANCELED:
            return 0
        elif state in (NavigationStateMachine.UNASSIGNED, NavigationStateMachine.ASSIGNED, NavigationStateMachine.RECEIVED, NavigationStateMachine.UNKNOWN):
            # Pre-run states still mean "in progress" from user's perspective.
            return 5
        elif state == NavigationStateMachine.STARTING:
            return 10
        elif state in (NavigationStateMachine.RUNNING, NavigationStateMachine.CANCELING):
            return 50
        else:
            return 0
    
    async def _publish_navigation_status(self, status: NavigationStatus, *, update_store: bool = True) -> None:
        """Publish navigation status to MQTT."""
        from services.nav_status_publisher import publish_navigation_status as _shared_publish
        await _shared_publish(status, self.mqtt_adapter, update_store=update_store)
    
    async def _publish_position_status(self, position: PositionStatus) -> None:
        """Publish position status to MQTT."""
        try:
            await state_store.set_last_position_status(position)
            _LOGGER.debug("Position saved to state_store: %s", position.model_dump())
            if self.mqtt_adapter:
                await self.mqtt_adapter.publish_position_status(position.model_dump())
                _LOGGER.debug("Position published to MQTT")
            # Drive distance-based progress updates from live position.
            await self._update_progress_from_position(position)
        except Exception as e:
            _LOGGER.error("Failed to publish position status: %s", e, exc_info=True)
            raise

    async def _update_progress_from_position(self, position: PositionStatus) -> None:
        """Update sessions using current position and publish status/event progress."""
        # Publish/update only the current command (single-active policy).
        active = await state_store.get_active_commands_for_publishing()
        if not active:
            return
        # Still iterate defensively over the (single) current command dict.
        for command_id, t in active.items():
            if NavigationStateMachine.is_terminal_state(t.state):
                self._at_goal_since.pop(command_id, None)  # cleanup dwell timer on terminal
                continue
            # P0-2 fix: CANCELING (state=9) is NOT terminal per is_terminal_state,
            # but we must NOT force-arrive during cancel. Skip entirely.
            if t.state == NavigationStateMachine.CANCELING:
                self._at_goal_since.pop(command_id, None)
                continue
            session = await state_store.get_session(command_id)
            if session is None:
                continue
            session = update_session_from_current(session, Pose2D(x=position.x, y=position.y, map_id=None))
            await state_store.upsert_session(session)

            progress = int(session.progress_percent or 0)
            # Optional safety-net: if controller never emits FINISHED but we are really at goal,
            # signal _watch_transport to finalize (single-owner pattern: only _watch_transport
            # executes terminal actions to prevent the double-ARRIVED race condition).
            if settings.symovo_force_arrival_on_proximity:
                try:
                    remaining = math.hypot(float(position.x) - float(session.goal.x), float(position.y) - float(session.goal.y))
                    if remaining <= float(settings.symovo_arrival_dist_m or 0.25):
                        now = time.time()
                        start = self._at_goal_since.get(command_id)
                        if start is None:
                            self._at_goal_since[command_id] = now
                        elif now - start >= float(settings.symovo_arrival_dwell_s or 2.5):
                            _LOGGER.warning(
                                "Force ARRIVED flag set: command=%s remaining=%.3fm progress=%s state=%s",
                                command_id,
                                remaining,
                                progress,
                                t.state,
                            )
                            # P0-1 fix: do NOT execute terminal actions here.
                            # Set flag for _watch_transport to pick up on its next iteration.
                            self._force_arrived_commands.add(command_id)
                            self._at_goal_since.pop(command_id, None)
                            return
                    else:
                        # Not at goal anymore
                        self._at_goal_since.pop(command_id, None)
                except Exception:
                    # Never let fallback logic break status publishing
                    _LOGGER.debug("Force-arrival proximity check failed", exc_info=True)

            status_enum = TransportOrchestrator.map_symovo_to_aehub(t.state)
            await self._publish_navigation_status(
                NavigationStatus(
                    status=status_enum,
                    goal_id=command_id,
                    progress_percent=progress,
                    error_reason=None,
                )
            )
            await self.bus.publish(
                StateProgressEvent(
                    type=StateType.PROGRESS.value,
                    command_id=command_id,
                    progress_percent=progress,
                    eta_seconds=None,
                )
            )

    async def _get_progress_for_command(self, command_id: str, *, fallback_state: int) -> int:
        """Prefer session-based progress; fallback to coarse state-based progress."""
        # Don't update progress for terminal states
        if NavigationStateMachine.is_terminal_state(fallback_state):
            session = await state_store.get_session(command_id)
            if session is not None:
                return int(session.progress_percent or 0)
            return self._calculate_progress(fallback_state, {})
        
        session = await state_store.get_session(command_id)
        if session is None:
            return self._calculate_progress(fallback_state, {})
        # If we have a recent position, update session first.
        pos = await state_store.get_last_position_status()
        if pos is not None:
            session = update_session_from_current(session, Pose2D(x=pos.x, y=pos.y, map_id=None))
            await state_store.upsert_session(session)
        return int(session.progress_percent or 0)

    async def _set_session_terminal(self, command_id: str, *, progress_percent: int) -> None:
        session = await state_store.get_session(command_id)
        if session is None:
            return
        session.progress_percent = int(progress_percent)
        session.min_remaining_dist_m = 0.0
        await state_store.upsert_session(session)
