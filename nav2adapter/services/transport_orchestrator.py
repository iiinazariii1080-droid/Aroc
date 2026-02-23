"""
Transport orchestrator for managing Symovo transport lifecycle.
"""
import logging
import time
from typing import Optional, Dict, Any
from services.symovo_service import SymovoAgvClient
from domain.state_machine import NavigationStateMachine
from domain.models import NavigationStatusEnum

_LOGGER = logging.getLogger(__name__)


class TransportOrchestrator:
    """Orchestrates transport creation, starting, and stopping."""
    
    def __init__(self, symovo_client: SymovoAgvClient):
        self.symovo_client = symovo_client
    
    async def create_transport_to_station(
        self,
        station_id: int,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a transport to a station using GoToStationStep.
        
        Args:
            station_id: Symovo station ID
            description: Optional transport description
            
        Returns:
            Transport data from Symovo API
        """
        # GoToStationStep type_id is typically 1 (check OpenAPI spec)
        step: Dict[str, Any] = {
            "station_id": station_id,
            "_type_id": 1,  # GoToStationStep
            "finished": False,
            "isNext": False,
        }
        
        state_log_item: Dict[str, Any] = {
            "timestamp": time.time(),
            "step_idx": 0,
            "status_code": 0,
            "status_detail": 1,
            "level": None,
        }
        
        payload: Dict[str, Any] = {
            "timestamp": time.time(),
            "id": 0,
            "agv": {"id": int(self.symovo_client.robot_number)},
            "job": None,
            "steps": [step],
            "state_log": [state_log_item],
            "needed_agv_attributes": {
                "full_eurobox": False,
                "half_eurobox_front": False,
                "half_eurobox_back": False,
                "gap_charge": False,
                "charging_contacts": False,
            },
            "description": description or f"GoTo Station {station_id}",
            "cancelable": True,
        }
        
        # Use guarded method to serialize mutating operations
        result = await self.symovo_client.transport_create(payload)
        
        return result
    
    async def create_transport_to_pose(
        self,
        x_m: float,
        y_m: float,
        theta_rad: float = 0.0,
        map_id: Optional[Any] = None,
        max_speed_m_s: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Create a transport to a pose.
        
        Args:
            x_m: X coordinate in meters
            y_m: Y coordinate in meters
            theta_rad: Heading in radians
            map_id: Map ID
            max_speed_m_s: Max speed in m/s
            
        Returns:
            Transport data from Symovo API
        """
        # Use existing method from SymovoAgvClient
        return await self.symovo_client.transport_move_to_pose(
            x_m=x_m,
            y_m=y_m,
            theta_rad=theta_rad,
            map_id=map_id,
            max_speed_m_s=max_speed_m_s,
            wait=False  # Don't wait, just create
        )
    
    async def start_transport(self, transport_id: str) -> Dict[str, Any]:
        """
        Start a transport.
        
        Args:
            transport_id: Symovo transport ID
            
        Returns:
            Response from Symovo API
        """
        # Use guarded method to serialize mutating operations
        return await self.symovo_client.transport_start(transport_id)
    
    async def stop_transport(self, transport_id: str) -> Dict[str, Any]:
        """
        Stop (cancel) a transport.
        
        Args:
            transport_id: Symovo transport ID
            
        Returns:
            Response from Symovo API
        """
        # Use guarded method to serialize mutating operations
        return await self.symovo_client.transport_stop(transport_id)

    async def delete_transport(self, transport_id: str) -> bool:
        """
        Delete a transport from the controller (best-effort cleanup).

        Used to remove orphaned transports that were created but never
        successfully started.  Swallows all exceptions so callers in
        error-handling paths are never disrupted.

        Args:
            transport_id: Symovo transport ID

        Returns:
            True if deletion succeeded, False otherwise.
        """
        try:
            await self.symovo_client.delete(
                f"/transport/{transport_id}", op_timeout=5.0
            )
            _LOGGER.warning(
                "Cleaned up orphaned transport %s after failed start",
                transport_id,
            )
            return True
        except Exception as exc:
            _LOGGER.warning(
                "Failed to cleanup orphaned transport %s: %s",
                transport_id,
                exc,
            )
            return False

    @staticmethod
    def map_symovo_to_aehub(symovo_state: int) -> NavigationStatusEnum:
        """
        Map Symovo transport state to AE.HUB navigation status enum.
        
        Args:
            symovo_state: Symovo TransportState enum value
            
        Returns:
            NavigationStatusEnum (idle/navigating/arrived/error)
        """
        return NavigationStateMachine.map_symovo_to_aehub(symovo_state)
    
    @staticmethod
    def get_transport_state(transport_data: Dict[str, Any]) -> int:
        """
        Extract transport state from transport data.
        
        Args:
            transport_data: Transport data from Symovo API
            
        Returns:
            Transport state integer
        """
        return transport_data.get("state", NavigationStateMachine.UNKNOWN)
    
    @staticmethod
    def is_terminal_state(transport_data: Dict[str, Any]) -> bool:
        """Check if transport is in terminal state."""
        state = TransportOrchestrator.get_transport_state(transport_data)
        return NavigationStateMachine.is_terminal_state(state)
    
    @staticmethod
    def is_active_state(transport_data: Dict[str, Any]) -> bool:
        """Check if transport is actively executing."""
        state = TransportOrchestrator.get_transport_state(transport_data)
        return NavigationStateMachine.is_active_state(state)
