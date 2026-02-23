import logging
import time
from typing import Optional, Dict, Any, List

_LOGGER = logging.getLogger(__name__)

_last_status: Optional[Dict[str, Any]] = None
_last_raw: Optional[Dict[str, Any]] = None
_last_updated_ts: float = 0.0


def _parse_devices_status_report(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw UFactory Studio WS 'devices_status_report' into the
    flat dict expected by get_robot_system_status().

    data layout (from UFactory Studio WS protocol):
      [0]  bool   – connected
      [2]  str    – IP address
      [4]  int    – state code
      [5]  list   – [state, mode, ??]
      [6]  int    – error code
      [7]  int    – warn code
    """
    data: List = msg.get("data", [])
    if not isinstance(data, list) or len(data) < 8:
        return msg  # not the expected shape – return as-is

    connected = bool(data[0]) if len(data) > 0 else False
    state_code = int(data[4]) if len(data) > 4 else 0
    error_code = int(data[6]) if len(data) > 6 else 0
    warn_code = int(data[7]) if len(data) > 7 else 0

    return {
        "alive": state_code in (1, 2, 6),
        "connected": connected,
        "state_code": state_code,
        "has_err_warn": (error_code != 0 or warn_code != 0),
        "has_error": (error_code != 0),
        "has_warn": (warn_code != 0),
        "error_code": error_code,
    }


def set_status(status: Dict[str, Any]) -> None:
    global _last_status, _last_raw, _last_updated_ts
    _last_raw = status
    _last_status = _parse_devices_status_report(status)
    _last_updated_ts = time.time()


def get_status() -> Optional[Dict[str, Any]]:
    return _last_status


def get_raw() -> Optional[Dict[str, Any]]:
    return _last_raw


def get_last_updated_ts() -> float:
    return _last_updated_ts


