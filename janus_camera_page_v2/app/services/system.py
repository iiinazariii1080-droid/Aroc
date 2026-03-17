from __future__ import annotations

from typing import Any, Dict

from app.core.settings import get_settings
from app.utils.process import run_cmd


def service_restart() -> None:
    settings = get_settings()
    run_cmd(["sudo", "systemctl", "restart", settings.service_name], timeout=30)



def systemd_brief(unit: str | None = None) -> Dict[str, Any]:
    service = unit or get_settings().service_name
    output = run_cmd(
        [
            "systemctl",
            "show",
            service,
            "-p",
            "ActiveState",
            "-p",
            "ActiveEnterTimestamp",
            "-p",
            "NRestarts",
        ],
        timeout=5,
    )
    kv = dict(line.split("=", 1) for line in output.strip().splitlines() if "=" in line)
    return {
        "active": kv.get("ActiveState") == "active",
        "since": kv.get("ActiveEnterTimestamp"),
        "restarts": int(kv.get("NRestarts", "0")),
    }
