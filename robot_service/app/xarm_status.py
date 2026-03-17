import logging
import time
from typing import Optional, Dict, Any, List

_LOGGER = logging.getLogger(__name__)

_last_status: Optional[Dict[str, Any]] = None
_last_raw: Optional[Dict[str, Any]] = None
_last_updated_ts: float = 0.0

# ---------------------------------------------------------------------------
# In-memory velocity setting — part of the robot live state.
# Populated lazily from DB on first read; written to DB on every set so the
# value survives a container restart.
# ---------------------------------------------------------------------------
_velocity_percent: Optional[float] = None  # None = "not yet loaded from DB"


def get_velocity_percent() -> float:
    """Return the current global velocity percent from in-memory state.

    On the very first call the value is loaded from the persistent DB so
    restarts are transparent.  Subsequent calls are pure in-memory reads.
    """
    global _velocity_percent
    if _velocity_percent is None:
        try:
            from db.settings import get_velocity_percent as _db_get
            _velocity_percent = _db_get()
        except Exception as e:
            _LOGGER.warning("xarm_status: could not load velocity from DB: %s", e)
            _velocity_percent = 20.0
    return _velocity_percent


def set_velocity_percent(v: float) -> None:
    """Update the in-memory velocity and persist it to DB."""
    global _velocity_percent
    v = max(1.0, min(100.0, float(v)))
    _velocity_percent = v
    try:
        from db.settings import set_velocity_percent as _db_set
        _db_set(v)
    except Exception as e:
        _LOGGER.warning("xarm_status: could not persist velocity to DB: %s", e)


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
      [39] float  – speed factor (0.0–1.0); multiply by 100 for percent
    """
    data: List = msg.get("data", [])
    if not isinstance(data, list) or len(data) < 8:
        return msg  # not the expected shape – return as-is

    connected = bool(data[0]) if len(data) > 0 else False
    state_code = int(data[4]) if len(data) > 4 else 0
    error_code = int(data[6]) if len(data) > 6 else 0
    warn_code = int(data[7]) if len(data) > 7 else 0

    parsed: Dict[str, Any] = {
        "alive": state_code in (1, 2, 6),
        "connected": connected,
        "state_code": state_code,
        "has_err_warn": (error_code != 0 or warn_code != 0),
        "has_error": (error_code != 0),
        "has_warn": (warn_code != 0),
        "error_code": error_code,
    }

    if len(data) > 39:
        try:
            speed_factor = float(data[39])
            if speed_factor > 0:
                parsed["speed_percent"] = round(speed_factor * 100.0, 1)
        except (TypeError, ValueError):
            pass

    return parsed


def set_status(status: Dict[str, Any]) -> None:
    global _last_status, _last_raw, _last_updated_ts, _velocity_percent
    _last_raw = status
    _last_status = _parse_devices_status_report(status)
    _last_updated_ts = time.time()
    # Keep in-memory velocity in sync with what the hardware actually reports.
    speed_percent = _last_status.get("speed_percent")
    if speed_percent is not None:
        _velocity_percent = speed_percent


def get_status() -> Optional[Dict[str, Any]]:
    return _last_status


def get_raw() -> Optional[Dict[str, Any]]:
    return _last_raw


def get_last_updated_ts() -> float:
    return _last_updated_ts


