"""
Readiness checker for robot navigation readiness.
"""
from typing import Optional, Dict, Any
from domain.models import RobotReadiness


class ReadinessChecker:
    """Checks robot readiness for navigation (fail-closed policy)."""
    
    @staticmethod
    def check_readiness(agv_status: Dict[str, Any]) -> RobotReadiness:
        """
        Check robot readiness from Symovo AGV status.
        
        Fail-closed policy: if any check fails, robot is not ready.
        
        Args:
            agv_status: Symovo AGV status dictionary
            
        Returns:
            RobotReadiness with ready flag and error_detail
        """
        state_flags = agv_status.get("state_flags", None)
        if not isinstance(state_flags, dict):
            # Fail-closed, but make the reason explicit: controller payload doesn't match expectations.
            return RobotReadiness(
                ready=False,
                error_detail="not_ready:missing_state_flags",
            )
        
        # Priority order: safety/scanner issues first (these usually block enabling drive mode anyway),
        # then operational mode, then drive readiness.
        if state_flags.get("emergency_stop", False) or state_flags.get("emergency_stop_reset_request", False):
            return RobotReadiness(ready=False, error_detail="not_ready:emergency_stop")
        
        if state_flags.get("sfuse_blown", False):
            return RobotReadiness(ready=False, error_detail="not_ready:fuse_blown")
        
        if not state_flags.get("safety_cleared", False):
            return RobotReadiness(ready=False, error_detail="not_ready:safety_not_cleared")

        if state_flags.get("waiting_for_scanner", False):
            return RobotReadiness(ready=False, error_detail="not_ready:waiting_for_scanner")

        if state_flags.get("laser_timeout", False):
            return RobotReadiness(ready=False, error_detail="not_ready:laser_timeout")
        
        if state_flags.get("odom_timeout", False):
            return RobotReadiness(ready=False, error_detail="not_ready:odom_timeout")
        
        # Manual/push mode must be disabled before drive_mode can be set.
        if state_flags.get("drive_manual", False):
            return RobotReadiness(ready=False, error_detail="not_ready:drive_manual")

        if state_flags.get("robot_paused", False):
            return RobotReadiness(ready=False, error_detail="not_ready:robot_paused")

        if state_flags.get("charging_connector", False) or state_flags.get("charging", False):
            return RobotReadiness(ready=False, error_detail="not_ready:charging")

        if not state_flags.get("drive_ready", False):
            return RobotReadiness(ready=False, error_detail="not_ready:drive_not_ready")
        
        # All checks passed
        return RobotReadiness(ready=True, error_detail=None)
    
    @staticmethod
    def is_ready(agv_status: Dict[str, Any]) -> bool:
        """
        Quick check if robot is ready.
        
        Args:
            agv_status: Symovo AGV status dictionary
            
        Returns:
            True if ready, False otherwise
        """
        return ReadinessChecker.check_readiness(agv_status).ready
