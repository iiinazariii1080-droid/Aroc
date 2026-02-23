import os
import json
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class AppConfig:
    ws_url: str
    log_level: str
    watchdog_timeout: float
    hold_timeout: float
    heartbeat_interval: float
    reconnect_max_delay: int
    robot_ip: Optional[str] = None


def load_config() -> AppConfig:
    return AppConfig(
        ws_url=os.getenv("WS_URL", os.getenv("XARM_WS_URL", "ws://192.168.1.220:18333/ws?channel=prod&lang=en&v=1&id=test123")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        watchdog_timeout=float(os.getenv("WATCHDOG_TIMEOUT", "3.0")),
        hold_timeout=float(os.getenv("HOLD_TIMEOUT", "0.5")),
        heartbeat_interval=float(os.getenv("HEARTBEAT_INTERVAL", "3.0")),
        reconnect_max_delay=int(os.getenv("RECONNECT_MAX_DELAY", "10")),
        robot_ip=os.getenv("ROBOT_IP") or None,
    )


def load_robot_config_file(path: str = "robot_config.json") -> Optional[str]:
    """Load robot IP from a simple JSON file {"robot_ip": "<ip>"}. Returns IP or None."""
    try:
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        ip = data.get("robot_ip")
        return str(ip) if ip else None
    except Exception:
        return None


def build_ws_url_from_ip(ip: str, channel: str = "prod", lang: str = "en", version: str = "1", client_id: str = "test123") -> str:
    """Helper to build a ws URL if the deployment uses direct addressing by IP.
    This does not replace the default cloud URL used in main; it's an opt-in utility.
    """
    return f"ws://192.168.1.220:18333/ws?channel={channel}&lang={lang}&v={version}&id={client_id}"


