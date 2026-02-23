"""
Error mapping from Symovo to AE.HUB format.
"""
from typing import Optional, Dict, Any, List


class ErrorMapper:
    """Maps Symovo errors to AE.HUB error_reason format."""
    
    # Symovo error code mappings
    ERROR_CODE_MAP: Dict[int, str] = {
        # Emergency stop related
        1000: "estop",
        1001: "estop",
        
        # Navigation/timeout related
        2000: "navigation_timeout",
        2001: "navigation_timeout",
        2002: "navigation_timeout",
        2003: "navigation_timeout",
        2004: "navigation_timeout",
        
        # Scanner/laser related
        3001: "scanner_blocked",
        3002: "scanner_blocked",
        3003: "scanner_blocked",
        3004: "scanner_blocked",
        3005: "scanner_blocked",
        
        # Safety/fuse related
        4000: "fuse_blown",
        4001: "fuse_blown",
        4002: "fuse_blown",
        4003: "fuse_blown",
    }
    
    @staticmethod
    def map_state_flags_to_error(state_flags: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Map Symovo state_flags to error reason.
        
        Args:
            state_flags: Symovo AGV state_flags dictionary
            
        Returns:
            Error reason string or None
        """
        if not state_flags:
            return None
        
        # Check for emergency stop
        if state_flags.get("emergency_stop") or state_flags.get("emergency_stop_reset_request"):
            return "estop"
        
        # Check for odom timeout
        if state_flags.get("odom_timeout"):
            return "navigation_timeout"
        
        # Check for laser/scanner timeout
        if state_flags.get("laser_timeout") or state_flags.get("waiting_for_scanner"):
            return "scanner_blocked"
        
        # Check for robot paused
        if state_flags.get("robot_paused"):
            return "robot_paused"
        
        # Check for fuse blown
        if state_flags.get("sfuse_blown"):
            return "fuse_blown"
        
        return None
    
    @staticmethod
    def map_transport_error(
        transport_data: Dict[str, Any],
        state_flags: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Map transport error from state_log to error reason.
        
        Args:
            transport_data: Transport data from Symovo API
            state_flags: Optional AGV state_flags for additional context
            
        Returns:
            Error reason string or None
        """
        # First check state_flags
        if state_flags:
            error = ErrorMapper.map_state_flags_to_error(state_flags)
            if error:
                return error
        
        # Check transport state_log for errors
        state_log: List[Dict[str, Any]] = transport_data.get("state_log", [])
        if not state_log:
            return None
        
        # Find the most recent error entry (level is not null)
        for log_item in reversed(state_log):
            level = log_item.get("level")
            if level is not None:  # Error entry
                status_code = log_item.get("status_code", 0)
                status_detail = log_item.get("status_detail", 0)
                
                # Try to map by error code
                if status_code in ErrorMapper.ERROR_CODE_MAP:
                    return ErrorMapper.ERROR_CODE_MAP[status_code]
                
                # Fallback: use status_detail or generic error
                if status_code != 0:
                    return f"transport_error_{status_code}_{status_detail}"
        
        # Check if transport state is ERROR
        transport_state = transport_data.get("state")
        if transport_state == 7:  # ERROR
            return "transport_error"
        
        return None
    
    @staticmethod
    def get_error_reason(
        transport_data: Optional[Dict[str, Any]] = None,
        state_flags: Optional[Dict[str, Any]] = None,
        default_reason: str = "unknown_error"
    ) -> str:
        """
        Get error reason from transport data and state flags.
        
        Args:
            transport_data: Transport data from Symovo API
            state_flags: AGV state_flags
            default_reason: Default error reason if nothing found
            
        Returns:
            Error reason string
        """
        if transport_data:
            error = ErrorMapper.map_transport_error(transport_data, state_flags)
            if error:
                return error
        
        if state_flags:
            error = ErrorMapper.map_state_flags_to_error(state_flags)
            if error:
                return error
        
        return default_reason
