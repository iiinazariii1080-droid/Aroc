"""
Command handler for driveToPosition and cancel commands.
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Any
from domain.models import NavigationCommand
from services.state_store import StateStore
from domain.state_machine import NavigationStateMachine
from services.readiness_checker import ReadinessChecker
from services.symovo_service import SymovoAgvClient
from domain.models import NavigationStatus, NavigationStatusEnum, NavigationSession, Pose2D, TargetConfig
from exceptions import DeviceError
from domain.events import AckEvent, ResultErrorEvent, AckType, ResultType, StateExecutingEvent, ResultCanceledEvent
from services.event_bus import EventBus
from services.status_publishing import publish_nav_status
from app.config import settings
from services.navigation_progress import dist_m

_LOGGER = logging.getLogger(__name__)


class CommandHandler:
    """Handles navigation commands with idempotency."""
    
    def __init__(
        self,
        symovo_client: SymovoAgvClient,
        event_bus: EventBus,
        state_store: StateStore,
    ):
        self.symovo_client = symovo_client
        self.event_bus = event_bus
        self.state_store = state_store
        self._navigate_lock = asyncio.Lock()
        # Event used to cancel a pending laser-timeout wait when a newer command arrives.
        self._laser_cancel_event: Optional[asyncio.Event] = None
    
    async def handle_drive_to_position(self, command: NavigationCommand) -> NavigationStatus:
        """
        Handle driveToPosition command with idempotency.
        
        If a previous command is waiting for laser_timeout / waiting_for_scanner
        to clear, it is immediately cancelled and replaced by this new command
        (no queue — latest command wins).
        
        Args:
            command: Navigation command
            
        Returns:
            Navigation status
        """
        # Signal any pending laser-wait to abort — the new command replaces it.
        if self._laser_cancel_event is not None:
            _LOGGER.info(
                "New navigate command %s arrived; cancelling pending laser-wait",
                command.command_id,
            )
            self._laser_cancel_event.set()

        # Create a fresh cancel event for *this* command's potential laser wait.
        cancel_event = asyncio.Event()
        self._laser_cancel_event = cancel_event

        # Serialize navigate flow to prevent concurrent commands from both
        # passing the busy check and creating orphan transports.
        try:
            async with asyncio.timeout(120):
                async with self._navigate_lock:
                    return await self._handle_drive_to_position_inner(command, cancel_event)
        except TimeoutError:
            _LOGGER.error("Navigate lock acquisition timed out (120s) for command %s", command.command_id)
            return NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command.command_id,
                progress_percent=0,
                error_reason="navigate_lock_timeout",
            )


    async def _handle_drive_to_position_inner(
        self, command: NavigationCommand, cancel_event: Optional[asyncio.Event] = None,
    ) -> NavigationStatus:
        """Inner implementation of drive_to_position, called under _navigate_lock."""
        _LOGGER.info("Handling driveToPosition command: %s, target: %s", command.command_id, command.target_id)

        # ACK received after basic parsing/validation
        await self.event_bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id=command.command_id))
        
        # 1. Resolve target from command coordinates
        target_config = self._resolve_target_config(command)
        if not target_config:
            await self.event_bus.publish(
                ResultErrorEvent(
                    type=ResultType.ERROR.value,
                    command_id=command.command_id,
                    reason=f"invalid_target_id:{command.target_id}",
                )
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command.command_id,
                progress_percent=0,
                error_reason=f"invalid_target_id:{command.target_id}",
            )
            await self._publish_status(error_status)
            return error_status
        
        # 2. Check idempotency
        existing_transport = await self.state_store.get_active_transport(command.command_id)
        if existing_transport:
            # If the same command_id is reused with a different target, treat it as a conflict.
            # This is a common operator mistake when testing with mosquitto_pub.
            if (
                isinstance(existing_transport.target_id, str)
                and existing_transport.target_id
                and existing_transport.target_id != command.target_id
            ):
                await self.event_bus.publish(
                    ResultErrorEvent(
                        type=ResultType.ERROR.value,
                        command_id=command.command_id,
                        reason=f"conflict:command_id_reuse:{existing_transport.target_id}->{command.target_id}",
                    )
                )
                error_status = NavigationStatus(
                    status=NavigationStatusEnum.ERROR,
                    goal_id=command.command_id,
                    progress_percent=0,
                    error_reason=f"conflict:command_id_reuse:{existing_transport.target_id}->{command.target_id}",
                )
                await self._publish_status(error_status)
                return error_status

            _LOGGER.info("Command %s already exists, returning current status", command.command_id)
            # Return current status (will be updated by status publisher)
            last_status = await self.state_store.get_last_navigation_status()
            if last_status and last_status.goal_id == command.command_id:
                return last_status
            # Fallback: create status from transport state
            return await self._get_status_from_transport(existing_transport)

        # Busy policy: reject if any transport is still in a non-terminal state
        # (P2-11: pre-run states 0-3 also block, not just STARTING/RUNNING/CANCELING)
        active = await self.state_store.get_all_active_commands()
        for _, t in active.items():
            if not NavigationStateMachine.is_terminal_state(t.state):
                await self.event_bus.publish(
                    ResultErrorEvent(type=ResultType.ERROR.value, command_id=command.command_id, reason="busy")
                )
                error_status = NavigationStatus(
                    status=NavigationStatusEnum.ERROR,
                    goal_id=command.command_id,
                    progress_percent=0,
                    error_reason="busy"
                )
                await self._publish_status(error_status)
                return error_status
        
        # 3. Check readiness (fail-closed)
        # Use uncached status for readiness checks to ensure we see current state
        # Add timeout to prevent hanging if controller is unreachable
        try:
            # Use shorter timeout for readiness check (5 seconds) to fail fast
            try:
                agv_status = await asyncio.wait_for(
                    self.symovo_client.status_uncached(),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                _LOGGER.error("Timeout checking robot readiness (5s). Controller may be unreachable.")
                raise DeviceError("Controller unreachable: timeout checking status")
            
            readiness = ReadinessChecker.check_readiness(agv_status)

            # --- Wait for scanner flags (laser_timeout / waiting_for_scanner) ---
            _SCANNER_FLAGS = {"not_ready:laser_timeout", "not_ready:waiting_for_scanner"}
            if (
                not readiness.ready
                and readiness.error_detail in _SCANNER_FLAGS
                and cancel_event is not None
            ):
                waited_readiness = await self._wait_for_scanner_clear(
                    cancel_event, command.command_id
                )
                if waited_readiness is None:
                    # Cancelled by newer command or timed out
                    if cancel_event.is_set():
                        reason = "replaced_by_newer_command"
                    else:
                        reason = readiness.error_detail or "not_ready:laser_timeout_expired"
                    await self.event_bus.publish(
                        ResultErrorEvent(
                            type=ResultType.ERROR.value,
                            command_id=command.command_id,
                            reason=reason,
                        )
                    )
                    error_status = NavigationStatus(
                        status=NavigationStatusEnum.ERROR,
                        goal_id=command.command_id,
                        progress_percent=0,
                        error_reason=reason,
                    )
                    await self._publish_status(error_status)
                    return error_status
                # Use the updated readiness (may be ready=True or a different error)
                readiness = waited_readiness

            if not readiness.ready:
                # Optional auto-action: try to switch controller into drive mode once.
                # Disabled by default; enable with SYMOVO_AUTO_SET_DRIVE_MODE=true.
                if (
                    readiness.error_detail == "not_ready:drive_not_ready"
                    and settings.symovo_auto_set_drive_mode
                ):
                    _LOGGER.info("drive_ready=false; attempting to set drive_mode via Symovo API...")
                    try:
                        await self.symovo_client.set_drive_mode()
                        await asyncio.sleep(float(settings.symovo_auto_set_drive_mode_wait_s or 1.0))
                        # Re-check with timeout
                        try:
                            agv_status = await asyncio.wait_for(
                                self.symovo_client.status_uncached(),
                                timeout=5.0
                            )
                            readiness = ReadinessChecker.check_readiness(agv_status)
                        except asyncio.TimeoutError:
                            _LOGGER.warning("Timeout re-checking readiness after drive_mode attempt")
                            readiness = ReadinessChecker.check_readiness({})  # Will fail readiness check
                    except Exception as e:
                        _LOGGER.warning("Failed to set drive_mode: %s", str(e))

                # IMPORTANT: if readiness became OK after auto-action, continue with navigation.
                if readiness.ready:
                    _LOGGER.info("Robot readiness recovered after drive_mode attempt; continuing")
                else:
                # Helpful diagnostics: show controller flags that blocked motion.
                    try:
                        _LOGGER.warning(
                            "Navigation not ready: %s (state_flags=%s)",
                            readiness.error_detail,
                            agv_status.get("state_flags"),
                        )
                    except Exception:
                        # Never allow logging failures to affect command handling
                        pass
                    await self.event_bus.publish(
                        ResultErrorEvent(type=ResultType.ERROR.value, command_id=command.command_id, reason=readiness.error_detail or "not_ready")
                    )
                    error_status = NavigationStatus(
                        status=NavigationStatusEnum.ERROR,
                        goal_id=command.command_id,
                        progress_percent=0,
                        error_reason=readiness.error_detail or "not_ready"
                    )
                    await self._publish_status(error_status)
                    return error_status
        except Exception as e:
            error_msg = str(e)
            _LOGGER.error(
                "Failed to check readiness: %s. Controller may be unreachable or overloaded. "
                "Check network connectivity and controller status.",
                error_msg
            )
            # More specific error reason based on exception type
            if "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                reason = "controller_timeout"
            else:
                reason = "machine_unreachable"
            
            await self.event_bus.publish(
                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command.command_id, reason=reason)
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command.command_id,
                progress_percent=0,
                error_reason=reason
            )
            await self._publish_status(error_status)
            return error_status
        
        # 5. Create and start transport
        transport_id: Optional[str] = None  # track for cleanup on error
        try:
            # Optional: hard reset history before starting each new navigation.
            # This enforces "only latest command" UX and prevents stale transports from lingering on controller.
            if settings.symovo_clear_transports_before_navigate:
                _LOGGER.warning(
                    "SYMOVO_CLEAR_TRANSPORTS_BEFORE_NAVIGATE=true: clearing ALL Symovo transports before starting %s",
                    command.command_id,
                )
                try:
                    await self.symovo_client.clear_all_transports()
                except Exception as e:
                    _LOGGER.warning("Failed to clear Symovo transports before navigate: %s", str(e), exc_info=True)

                # Also clear local history/persistence so old command_ids cannot publish/compete.
                try:
                    cleared_local = await self.state_store.clear_all_commands()
                    if cleared_local:
                        _LOGGER.info("Cleared %s local command(s) before navigate", cleared_local)
                except Exception as e:
                    _LOGGER.warning("Failed to clear local command history before navigate: %s", str(e), exc_info=True)

            # Create navigation session (start/goal) for distance-based progress.
            # Best-effort: do not fail command if we cannot capture a pose snapshot.
            await self._ensure_navigation_session(command, target_config)

            if target_config.is_station:
                transport_data = await self.symovo_client.transport_create_station(
                    station_id=target_config.station_id,
                    description=f"Navigate to {command.target_id}"
                )
            else:
                transport_data = await self.symovo_client.transport_move_to_pose(
                    x_m=target_config.x,
                    y_m=target_config.y,
                    theta_rad=target_config.theta or 0.0,
                    map_id=target_config.map_id,
                    max_speed_m_s=target_config.max_speed_m_s,
                    wait=False,
                )
            
            transport_id = str(transport_data.get("id", ""))
            if not transport_id:
                raise DeviceError("Transport creation failed: no transport ID returned")
            
            # Start transport (with timeout to prevent holding the lock forever)
            await asyncio.wait_for(
                self.symovo_client.transport_start(transport_id),
                timeout=30.0,
            )
            
            # 6. Register in state store
            transport_state = transport_data.get("state", 0)
            await self.state_store.register_command(
                command_id=command.command_id,
                transport_id=transport_id,
                state=transport_state,
                target_id=command.target_id
            )

            # ACK accepted after successful create+start
            await self.event_bus.publish(AckEvent(type=AckType.ACCEPTED.value, command_id=command.command_id))
            await self.event_bus.publish(StateExecutingEvent(type="state.executing", command_id=command.command_id))
            
            # 7. Publish initial status
            status = NavigationStatus(
                status=NavigationStatusEnum.NAVIGATING,
                goal_id=command.command_id,
                progress_percent=1,  # Just started
                error_reason=None
            )
            await self._publish_status(status)
            
            _LOGGER.info("Successfully started transport %s for command %s", transport_id, command.command_id)
            return status
            
        except asyncio.CancelledError:
            # Re-raise CancelledError cleanly — must not be caught by Exception below
            if transport_id:
                try:
                    await asyncio.shield(self.symovo_client.delete_transport(transport_id))
                except Exception:
                    _LOGGER.debug("Cleanup delete_transport failed on cancel", exc_info=True)
            try:
                await self.state_store.clear_session(command.command_id)
            except Exception:
                pass
            raise
        except Exception as e:
            _LOGGER.error("Failed to create/start transport: %s", e)

            # Best-effort cleanup: if the transport was created on the
            # controller but the subsequent start (or registration) failed,
            # delete it so it does not linger as an orphan.
            if transport_id:
                try:
                    await asyncio.shield(self.symovo_client.delete_transport(transport_id))
                except Exception:
                    _LOGGER.debug("Cleanup delete_transport failed", exc_info=True)

            # P2-7: clear orphaned NavigationSession that was persisted before
            # transport creation.  Without this, the session lingers on disk.
            try:
                await self.state_store.clear_session(command.command_id)
            except Exception:
                _LOGGER.debug("Cleanup clear_session failed", exc_info=True)

            await self.event_bus.publish(
                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command.command_id, reason="transport_creation_failed")
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command.command_id,
                progress_percent=0,
                error_reason="transport_creation_failed"
            )
            await self._publish_status(error_status)
            return error_status

    # ------------------------------------------------------------------
    # Scanner-flag wait (laser_timeout / waiting_for_scanner)
    # ------------------------------------------------------------------

    async def _wait_for_scanner_clear(
        self,
        cancel_event: asyncio.Event,
        command_id: str,
    ) -> Optional["RobotReadiness"]:
        """Poll controller status until scanner flags clear or timeout/cancel.

        Returns:
            * ``RobotReadiness(ready=True, ...)`` when the scanner issue resolved
              and the robot is fully ready.
            * A non-scanner ``RobotReadiness(ready=False, ...)`` if a *different*
              readiness problem is detected (caller should handle normally).
            * ``None`` if the wait was cancelled (``cancel_event`` set) or the
              timeout expired without recovery.
        """
        from domain.models import RobotReadiness  # local to avoid circular at module level

        _SCANNER_FLAGS = {"not_ready:laser_timeout", "not_ready:waiting_for_scanner"}

        timeout = float(settings.laser_timeout_wait_s)
        poll_interval = float(settings.laser_timeout_poll_interval_s)
        deadline = time.monotonic() + timeout

        _LOGGER.info(
            "Scanner flag active for command %s — waiting up to %.0fs for it to clear",
            command_id, timeout,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _LOGGER.warning(
                    "Scanner-wait timed out (%.0fs) for command %s", timeout, command_id,
                )
                return None

            # Sleep / wait for cancel — whichever comes first.
            wait_time = min(poll_interval, remaining)
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=wait_time)
                # cancel_event was set → a newer command replaced us
                _LOGGER.info(
                    "Scanner-wait cancelled for command %s (replaced by newer command)",
                    command_id,
                )
                return None
            except asyncio.TimeoutError:
                # Normal poll tick — continue to re-check status below
                pass

            # Re-fetch status
            try:
                agv_status = await asyncio.wait_for(
                    self.symovo_client.status_uncached(),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                _LOGGER.warning(
                    "Status fetch failed during scanner-wait for %s: %s", command_id, exc,
                )
                continue  # retry on next tick

            readiness = ReadinessChecker.check_readiness(agv_status)

            if readiness.ready:
                _LOGGER.info(
                    "Scanner flag cleared for command %s — proceeding with navigation",
                    command_id,
                )
                return readiness

            if readiness.error_detail not in _SCANNER_FLAGS:
                # Different problem appeared (e.g. emergency_stop) — return it
                # so the caller handles it through the normal error path.
                _LOGGER.warning(
                    "Different readiness issue appeared during scanner-wait for %s: %s",
                    command_id, readiness.error_detail,
                )
                return readiness

            # Still a scanner flag — keep waiting
            _LOGGER.debug(
                "Scanner flag still active for %s (%s), %.1fs remaining",
                command_id, readiness.error_detail, deadline - time.monotonic(),
            )

    async def _ensure_navigation_session(self, command: NavigationCommand, target_config: TargetConfig) -> None:
        """Create/update NavigationSession for the command if not present."""
        try:
            existing = await self.state_store.get_session(command.command_id)
            if existing is not None:
                return

            if target_config.is_station:
                _LOGGER.debug(
                    "Skipping navigation session for %s: station-only target",
                    command.command_id,
                )
                return

            goal = Pose2D(
                x=float(target_config.x),
                y=float(target_config.y),
                map_id=target_config.map_id,
            )

            start_pos = await self.state_store.get_last_position_status()
            if start_pos is not None:
                start = Pose2D(x=float(start_pos.x), y=float(start_pos.y), map_id=None)
            else:
                start = await self._fetch_start_pose_from_symovo()

            if start is None:
                _LOGGER.warning(
                    "Cannot determine start pose for %s; skipping session "
                    "(fallback progress will be used)",
                    command.command_id,
                )
                return

            total = dist_m(start, goal)
            session = NavigationSession(
                command_id=command.command_id,
                target_id=command.target_id,
                start=start,
                goal=goal,
                total_dist_m=total,
                min_remaining_dist_m=total,
                # Start at 1% so we never regress from the initial "just started" status.
                # The progress function is monotonic and will keep this as a floor until real progress increases.
                progress_percent=1,
            )
            await self.state_store.upsert_session(session)
        except Exception as e:
            _LOGGER.debug("Failed to create navigation session for %s: %s", command.command_id, str(e))

    async def _fetch_start_pose_from_symovo(self) -> Optional[Pose2D]:
        """Best-effort: fetch current pose from Symovo in various response shapes.

        Returns None when pose cannot be determined (logs a warning).
        """
        data = await self.symovo_client.pose()
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            _LOGGER.warning("Symovo pose returned non-dict (%s); cannot determine start pose", type(data).__name__)
            return None

        pose = None
        if isinstance(data.get("pose"), dict):
            pose = data.get("pose")
        elif isinstance(data.get("result"), dict) and isinstance(data["result"].get("pose"), dict):
            pose = data["result"]["pose"]
        else:
            pose = data

        x_val = pose.get("x") if isinstance(pose, dict) else None
        y_val = pose.get("y") if isinstance(pose, dict) else None
        if not isinstance(x_val, (int, float)) or not isinstance(y_val, (int, float)):
            _LOGGER.warning("Symovo pose missing or non-numeric x/y; cannot determine start pose")
            return None
        return Pose2D(x=float(x_val), y=float(y_val), map_id=None)
    
    async def handle_cancel(self, command_id: str) -> NavigationStatus:
        """
        Handle cancel command.
        
        Args:
            command_id: Command ID to cancel
            
        Returns:
            Navigation status
        """
        _LOGGER.info("Handling cancel command for: %s", command_id)
        await self.event_bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id=command_id))
        
        # Find active transport
        active_transport = await self.state_store.get_active_transport(command_id)
        if not active_transport:
            _LOGGER.warning("No active transport found for command %s (already completed or unknown)", command_id)
            # P2-8: only publish IDLE if no OTHER active commands are running.
            remaining = await self.state_store.get_all_active_commands()
            status = NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
                error_reason=None,
            )
            if remaining:
                _LOGGER.info("Skipping IDLE publish — %d other active command(s)", len(remaining))
            else:
                await self._publish_status(status)
            # Don't emit result.canceled for unknown/already-completed commands —
            # downstream consumers would record a misleading cancellation event.
            return status
        
        # Stop transport
        try:
            # Stop tracking distance-based progress for this command immediately.
            await self.state_store.clear_session(command_id)

            # P7-4 fix: Clear transport from state_store FIRST, before calling stop_transport.
            # This causes _watch_transport to see "no longer active" on its next check and
            # exit cleanly, preventing duplicate result.canceled events from both cancel
            # and the watcher.
            await self.state_store.clear_transport(command_id)

            # Publish cancel acknowledgment immediately
            await self.event_bus.publish(AckEvent(type=AckType.ACCEPTED.value, command_id=command_id))
            
            # Stop transport with timeout to prevent blocking
            # Use a reasonable timeout - stop operation should be quick
            try:
                await asyncio.wait_for(
                    self.symovo_client.transport_stop(active_transport.transport_id),
                    timeout=5.0  # 5 seconds should be enough for stop operation
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("stop_transport timed out for %s, but continuing with cancel", active_transport.transport_id)
            
            await self.event_bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
            
            status = NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
                error_reason=None
            )
            await self._publish_status(status)
            
            _LOGGER.info("Successfully canceled transport %s for command %s", active_transport.transport_id, command_id)
            return status
            
        except Exception as e:
            _LOGGER.error("Failed to cancel transport: %s", e)
            await self.event_bus.publish(
                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command_id, reason="cancel_failed")
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command_id,
                progress_percent=0,
                error_reason="cancel_failed"
            )
            await self._publish_status(error_status)
            return error_status

    def _resolve_target_config(self, command: NavigationCommand) -> Optional[TargetConfig]:
        """Extract target coordinates from command fields."""
        if command.station_id is not None:
            return TargetConfig(station_id=command.station_id, max_speed_m_s=command.max_speed_m_s)
        if command.x is not None and command.y is not None:
            return TargetConfig(
                x=float(command.x),
                y=float(command.y),
                theta=float(command.theta or 0.0),
                map_id=int(command.map_id or 0),
                max_speed_m_s=command.max_speed_m_s,
            )
        return None
    
    async def _get_status_from_transport(self, transport: Any) -> NavigationStatus:
        """Get navigation status from transport state."""
        try:
            transport_data = await self.symovo_client.transport_get(transport.transport_id)
            state = transport_data.get("state", 0)
            status_enum = NavigationStateMachine.map_symovo_to_aehub(state)
            
            return NavigationStatus(
                status=status_enum,
                goal_id=transport.command_id,
                progress_percent=50 if status_enum == NavigationStatusEnum.NAVIGATING else 0,
                error_reason=None
            )
        except Exception:
            # Fallback
            return NavigationStatus(
                status=NavigationStatusEnum.NAVIGATING,
                goal_id=transport.command_id,
                progress_percent=0,
                error_reason=None
            )
    
    async def _publish_status(self, status: NavigationStatus) -> None:
        """Store navigation status."""
        await publish_nav_status(status, self.state_store)
