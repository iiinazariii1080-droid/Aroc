import socket
import threading
from typing import Optional, Any
from xarm.wrapper import XArmAPI
from drivers.xarm_driver.xarm_manipulator import RobotMain


class XArmManager:
    """
    Manager for robot instance. When actor is provided, uses RobotActor's arm (single-owner SDK).
    When actor is None (legacy), creates XArmAPI itself.
    """

    def __init__(self, ip_address: str, actor: Optional[Any] = None):
        self.ip_address = ip_address
        self._actor = actor  # RobotActor when using V2 architecture
        self._lock = threading.RLock()
        self._instance = None
        if self._actor is None:
            self._create_new_instance()

    def get_instance(self, reset: bool = False):
        with self._lock:
            if self._actor is not None:
                arm = self._actor.get_arm()
                if arm is None:
                    self._instance = None
                    from app.exceptions import DeviceConnectionError
                    raise DeviceConnectionError("Robot not connected")
                if self._instance is not None and self._instance._arm is arm and not reset:
                    return self._instance
                self._instance = RobotMain(arm)
                return self._instance
            if (
                not reset
                and self._instance is not None
                and self._instance._arm.connected
            ):
                return self._instance
            self._create_new_instance()
            return self._instance

    def _create_new_instance(self):
        """Legacy: create XArmAPI directly. Only when actor is None."""
        self._disconnect_instance()
        host = self.ip_address
        try:
            host = socket.gethostbyname(host)
        except Exception:
            pass
        print("Connecting to:", host)
        arm = XArmAPI(host)
        arm.get_robot_sn()
        self._instance = RobotMain(arm)

    def _disconnect_instance(self):
        with self._lock:
            if self._instance and self._actor is None:
                try:
                    self._instance._arm.disconnect()
                except Exception:
                    pass
            self._instance = None
