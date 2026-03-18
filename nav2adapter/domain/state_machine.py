"""
State machine for navigation lifecycle.
"""
from typing import Optional
from domain.models import NavigationStatusEnum


class NavigationStateMachine:
    """State machine for navigation lifecycle."""
    
    # Symovo TransportState enum values
    UNKNOWN = 0
    UNASSIGNED = 1
    ASSIGNED = 2
    RECEIVED = 3
    STARTING = 4
    RUNNING = 5
    CANCELED = 6
    ERROR = 7
    FINISHED = 8
    CANCELING = 9
    
    @staticmethod
    def map_symovo_to_aehub(symovo_state: int) -> NavigationStatusEnum:
        """
        Map Symovo transport state to AE.HUB navigation status.
        
        Args:
            symovo_state: Symovo TransportState enum value
            
        Returns:
            AE.HUB navigation status
        """
        if symovo_state == NavigationStateMachine.FINISHED:
            return NavigationStatusEnum.ARRIVED
        elif symovo_state == NavigationStateMachine.ERROR:
            return NavigationStatusEnum.ERROR
        elif symovo_state == NavigationStateMachine.CANCELED:
            return NavigationStatusEnum.IDLE  # Canceled → idle
        else:
            # For an existing transport, treat all non-terminal states as "navigating".
            # Many controllers go through UNASSIGNED/ASSIGNED/RECEIVED (or even UNKNOWN=0)
            # while a transport is actively being scheduled/started.
            return NavigationStatusEnum.NAVIGATING
    
    @staticmethod
    def is_terminal_state(symovo_state: int) -> bool:
        """Check if transport state is terminal (FINISHED, ERROR, CANCELED)."""
        return symovo_state in (
            NavigationStateMachine.FINISHED,
            NavigationStateMachine.ERROR,
            NavigationStateMachine.CANCELED
        )
    
    @staticmethod
    def is_pre_run_state(symovo_state: int) -> bool:
        """Check if transport is in pre-run state (UNASSIGNED, ASSIGNED, RECEIVED).

        Pre-run transports are not yet actively executing; during recovery
        the controller will resolve them without our intervention.
        """
        return symovo_state in (
            NavigationStateMachine.UNASSIGNED,
            NavigationStateMachine.ASSIGNED,
            NavigationStateMachine.RECEIVED,
        )

    @staticmethod
    def is_active_state(symovo_state: int) -> bool:
        """Check if transport is actively executing (STARTING, RUNNING, CANCELING)."""
        return symovo_state in (
            NavigationStateMachine.STARTING,
            NavigationStateMachine.RUNNING,
            NavigationStateMachine.CANCELING
        )
