import time
from typing import Optional, Dict, Any


_last_status: Optional[Dict[str, Any]] = None
_last_updated_ts: float = 0.0


def set_status(status: Dict[str, Any]) -> None:
    global _last_status, _last_updated_ts
    _last_status = status
    _last_updated_ts = time.time()


def get_status() -> Optional[Dict[str, Any]]:
    return _last_status


def get_last_updated_ts() -> float:
    return _last_updated_ts


