"""
Command handler for navigateTo and cancel commands.
"""
import asyncio
import math
import logging
import time
from typing import Optional, Dict, Any
from domain.models import NavigationCommand
from services.state_store import state_store
from services.transport_orchestrator import TransportOrchestrator
from domain.state_machine import NavigationStateMachine
from services.readiness_checker import ReadinessChecker
from services.symovo_service import SymovoAgvClient
from services.mqtt_adapter import MqttAdapter
from domain.models import NavigationStatus, NavigationStatusEnum, NavigationSession, Pose2D
from exceptions import DeviceError
from domain.events import AckEvent, ResultErrorEvent, AckType, ResultType, StateExecutingEvent, ResultCanceledEvent
from services.event_bus import EventBus
from services.nav_status_publisher import publish_navigation_status as _shared_publish
from services import charger_workflow
from db.robot_positions import get_robot_position_by_name
from app.config import settings
from services.navigation_progress import dist_m

_LOGGER = logging.getLogger(__name__)


class CommandHandler:
    """Handles navigation commands with idempotency."""
    
    def __init__(
        self,
        symovo_client: SymovoAgvClient,
        transport_orchestrator: TransportOrchestrator,
        mqtt_adapter: Optional[MqttAdapter],
        event_bus: EventBus,
    ):
        self.symovo_client = symovo_client
        self.transport_orchestrator = transport_orchestrator
        self.mqtt_adapter = mqtt_adapter
        self.event_bus = event_bus
    
    async def handle_navigate_to(self, command: NavigationCommand) -> NavigationStatus:
        """
        Handle navigateTo command with idempotency.
        
        Args:
            command: Navigation command
            
        Returns:
            Navigation status
        """
        _LOGGER.info(f"Handling navigateTo command: {command.command_id}, target: {command.target_id}")

        # ACK received after basic parsing/validation
        await self.event_bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id=command.command_id))
        
        # 1. Resolve target_id from DB by unique name (case-insensitive)
        target_config = await self._resolve_target_config(command.target_id)
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
        existing_transport = await state_store.get_active_transport(command.command_id)
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

            _LOGGER.info(f"Command {command.command_id} already exists, returning current status")
            # Return current status (will be updated by status publisher)
            last_status = await state_store.get_last_navigation_status()
            if last_status and last_status.goal_id == command.command_id:
                return last_status
            # Fallback: create status from transport state
            return await self._get_status_from_transport(existing_transport)

        # Busy policy: if any active transport in STARTING/RUNNING/CANCELING, reject by default
        active = await state_store.get_all_active_commands()
        for _, t in active.items():
            if t.state in (NavigationStateMachine.STARTING, NavigationStateMachine.RUNNING, NavigationStateMachine.CANCELING):
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
                    remaining = await self.symovo_client.clear_all_transports()
                except Exception as e:
                    _LOGGER.warning("Failed to clear Symovo transports before navigate: %s", str(e), exc_info=True)

                # Also clear local history/persistence so old command_ids cannot publish/compete.
                try:
                    cleared_local = await state_store.clear_all_commands()
                    if cleared_local:
                        _LOGGER.info("Cleared %s local command(s) before navigate", cleared_local)
                except Exception as e:
                    _LOGGER.warning("Failed to clear local command history before navigate: %s", str(e), exc_info=True)

            # Create navigation session (start/goal) for distance-based progress.
            # Best-effort: do not fail command if we cannot capture a pose snapshot.
            await self._ensure_navigation_session(command, target_config)

            if "station_id" in target_config:
                # Station-based navigation
                station_id = target_config["station_id"]
                transport_data = await self.transport_orchestrator.create_transport_to_station(
                    station_id=station_id,
                    description=f"Navigate to {command.target_id}"
                )
            elif "x" in target_config and "y" in target_config:
                # Pose-based navigation
                x = target_config["x"]
                y = target_config["y"]
                theta = target_config.get("theta", 0.0)
                map_id = target_config.get("map_id", 0)
                max_speed = target_config.get("max_speed_m_s")

                # P1-7 fix: _resolve_target_config already converts theta_deg → radians.
                # The old heuristic (if abs(theta) > 2π → radians()) double-converted
                # angles > ~200°. Removed — theta is always in radians at this point.
                
                transport_data = await self.transport_orchestrator.create_transport_to_pose(
                    x_m=x,
                    y_m=y,
                    theta_rad=theta,
                    map_id=map_id,
                    max_speed_m_s=max_speed
                )
            else:
                error_status = NavigationStatus(
                    status=NavigationStatusEnum.ERROR,
                    goal_id=command.command_id,
                    progress_percent=0,
                    error_reason=f"invalid_position_config:{command.target_id}"
                )
                await self._publish_status(error_status)
                return error_status
            
            transport_id = str(transport_data.get("id", ""))
            if not transport_id:
                raise DeviceError("Transport creation failed: no transport ID returned")
            
            # Start transport
            await self.transport_orchestrator.start_transport(transport_id)
            
            # 6. Register in state store
            transport_state = transport_data.get("state", 0)
            await state_store.register_command(
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
            
            _LOGGER.info(f"Successfully started transport {transport_id} for command {command.command_id}")
            return status
            
        except Exception as e:
            _LOGGER.error(f"Failed to create/start transport: {e}")

            # Best-effort cleanup: if the transport was created on the
            # controller but the subsequent start (or registration) failed,
            # delete it so it does not linger as an orphan.
            if transport_id:
                await self.transport_orchestrator.delete_transport(transport_id)

            await self.event_bus.publish(
                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command.command_id, reason=f"transport_creation_failed:{str(e)}")
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command.command_id,
                progress_percent=0,
                error_reason=f"transport_creation_failed:{str(e)}"
            )
            await self._publish_status(error_status)
            return error_status

    async def _ensure_navigation_session(self, command: NavigationCommand, target_config: Dict[str, Any]) -> None:
        """Create/update NavigationSession for the command if not present."""
        try:
            existing = await state_store.get_session(command.command_id)
            if existing is not None:
                return

            # P0-3 fix: guard against missing x/y (e.g. station_id-only targets)
            # float(None) raises TypeError, so we must check first.
            x_val = target_config.get("x")
            y_val = target_config.get("y")
            if x_val is None or y_val is None:
                _LOGGER.debug(
                    "Skipping navigation session for %s: target has no x/y (station-only target)",
                    command.command_id,
                )
                return

            goal = Pose2D(
                x=float(x_val),
                y=float(y_val),
                map_id=int(target_config.get("map_id")) if isinstance(target_config.get("map_id"), int) else None,
            )

            start_pos = await state_store.get_last_position_status()
            if start_pos is not None:
                start = Pose2D(x=float(start_pos.x), y=float(start_pos.y), map_id=None)
            else:
                # Fallback to Symovo pose snapshot
                start = await self._fetch_start_pose_from_symovo()

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
            await state_store.upsert_session(session)
        except Exception as e:
            _LOGGER.debug("Failed to create navigation session for %s: %s", command.command_id, str(e))

    async def _fetch_start_pose_from_symovo(self) -> Pose2D:
        """Best-effort: fetch current pose from Symovo in various response shapes."""
        data = await self.symovo_client.pose()
        # Symovo may return list, dict, or wrapped dict
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return Pose2D(x=0.0, y=0.0, map_id=None)

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
            return Pose2D(x=0.0, y=0.0, map_id=None)
        return Pose2D(x=float(x_val), y=float(y_val), map_id=None)
    
    async def handle_cancel(self, command_id: str) -> NavigationStatus:
        """
        Handle cancel command.
        
        Args:
            command_id: Command ID to cancel
            
        Returns:
            Navigation status
        """
        _LOGGER.info(f"Handling cancel command for: {command_id}")
        await self.event_bus.publish(AckEvent(type=AckType.RECEIVED.value, command_id=command_id))
        
        # Find active transport
        active_transport = await state_store.get_active_transport(command_id)
        if not active_transport:
            _LOGGER.warning(f"No active transport found for command {command_id}")
            # Return idle status
            status = NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
                error_reason=None
            )
            await self._publish_status(status)
            await self.event_bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
            return status
        
        # Stop transport
        try:
            # Stop tracking distance-based progress for this command immediately.
            await state_store.clear_session(command_id)

            # P1-8 fix: Deactivate charger station if this was a charger navigation.
            # Must happen before clearing transport so the session/target_id is still available.
            await charger_workflow.maybe_deactivate_on_cancel(self.symovo_client, active_transport)
            
            # Update state store FIRST to signal cancellation to watchers
            # This ensures watchers can detect cancellation before making long-poll requests
            await state_store.update_transport_state(command_id, 9)  # CANCELING

            # Publish cancel acknowledgment immediately
            await self.event_bus.publish(AckEvent(type=AckType.ACCEPTED.value, command_id=command_id))
            
            # Stop transport with timeout to prevent blocking
            # Use a reasonable timeout - stop operation should be quick
            try:
                await asyncio.wait_for(
                    self.transport_orchestrator.stop_transport(active_transport.transport_id),
                    timeout=5.0  # 5 seconds should be enough for stop operation
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(f"stop_transport timed out for {active_transport.transport_id}, but continuing with cancel")
            
            # Clear transport from state store to stop watchers
            await state_store.clear_transport(command_id)
            
            await self.event_bus.publish(ResultCanceledEvent(type=ResultType.CANCELED.value, command_id=command_id))
            
            status = NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                goal_id=None,
                progress_percent=0,
                error_reason=None
            )
            await self._publish_status(status)
            
            _LOGGER.info(f"Successfully canceled transport {active_transport.transport_id} for command {command_id}")
            return status
            
        except Exception as e:
            _LOGGER.error(f"Failed to cancel transport: {e}")
            await self.event_bus.publish(
                ResultErrorEvent(type=ResultType.ERROR.value, command_id=command_id, reason=f"cancel_failed:{str(e)}")
            )
            error_status = NavigationStatus(
                status=NavigationStatusEnum.ERROR,
                goal_id=command_id,
                progress_percent=0,
                error_reason=f"cancel_failed:{str(e)}"
            )
            await self._publish_status(error_status)
            return error_status

    async def _resolve_target_config(self, target_id: str) -> Optional[Dict[str, Any]]:
        """Resolve target config ONLY from DB by unique name."""
        if not isinstance(target_id, str) or not target_id:
            return None

        # DB lookup by unique name
        try:
            rec = await asyncio.to_thread(get_robot_position_by_name, target_id)
        except Exception as e:
            _LOGGER.warning(f"DB lookup failed for target '{target_id}': {e}")
            return None

        if not isinstance(rec, dict):
            return None
        params = rec.get("params")
        if not isinstance(params, dict):
            return None

        # Extract pose from params. Preferred: params.location (x_m/y_m/theta_deg/map_id)
        loc = None
        for key in ("location", "car_position", "car_pposition"):
            if isinstance(params.get(key), dict):
                loc = params.get(key)
                break

        # Fallback: some payloads may store pose at top-level of params
        if not isinstance(loc, dict):
            loc = params

        # Accept x/y in meters (x_m/y_m or x/y)
        x_val = loc.get("x_m", loc.get("x"))
        y_val = loc.get("y_m", loc.get("y"))
        if not isinstance(x_val, (int, float)) or not isinstance(y_val, (int, float)):
            return None

        # theta: prefer theta_rad, otherwise theta_deg, otherwise theta (assume rad)
        theta_rad: float = 0.0
        if isinstance(loc.get("theta_rad"), (int, float)):
            theta_rad = float(loc.get("theta_rad"))
        elif isinstance(loc.get("theta_deg"), (int, float)):
            theta_rad = math.radians(float(loc.get("theta_deg")))
        elif isinstance(loc.get("theta"), (int, float)):
            theta_rad = float(loc.get("theta"))

        map_id = loc.get("map_id", 0)

        # Optional speed (if provided in same shape as mapping)
        max_speed = params.get("max_speed_m_s")
        if not isinstance(max_speed, (int, float)):
            max_speed = None

        return {
            "x": float(x_val),
            "y": float(y_val),
            "theta": float(theta_rad),
            "map_id": map_id,
            "max_speed_m_s": max_speed,
        }
    
    async def _get_status_from_transport(self, transport: Any) -> NavigationStatus:
        """Get navigation status from transport state."""
        try:
            transport_data = await self.symovo_client.transport_get(transport.transport_id)
            state = transport_data.get("state", 0)
            status_enum = TransportOrchestrator.map_symovo_to_aehub(state)
            
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
        """Publish status to MQTT and store it."""
        await _shared_publish(status, self.mqtt_adapter)
