import asyncio
import time
from typing import Optional, Any
from drivers.xarm_driver.XArmManager import XArmManager  # lazy import
from app.config import XARM_IP
from app.exceptions import DeviceConnectionError


def _bridge_state_store_connected(connected: bool) -> None:
    """Bridge: update StateStore when connection state is known (Phase 1)."""
    try:
        from app.di import get_state_store
        store = get_state_store()
        store.set_connected(connected)
        if connected:
            store.set_last_seen(time.time())
    except Exception:
        pass


xarm_manager: Optional[Any] = None
xarm_lock = asyncio.Lock()


def _create_manager():
    try:
        from app.di import get_actor
        return XArmManager(ip_address=XARM_IP, actor=get_actor())
    except Exception as e:
        raise DeviceConnectionError(f"xArm driver not available: {e}")
    


async def init_state():
    """Initialize app-level state. Connection state is owned by RobotActor."""
    global xarm_manager
    if xarm_manager is None:
        try:
            xarm_manager = _create_manager()
        except DeviceConnectionError:
            # Defer error to first endpoint call
            xarm_manager = None

async def shutdown_state():
    global xarm_manager
    _bridge_state_store_connected(False)
    # XArmManager closes on recreate; nothing persistent to close explicitly
    xarm_manager = None


def get_manager():
    """Return a manager instance, creating it if needed (legacy helper)."""
    global xarm_manager
    if xarm_manager is None:
        xarm_manager = _create_manager()
    return xarm_manager
