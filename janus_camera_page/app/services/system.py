from __future__ import annotations

import subprocess
from typing import Any, Dict

from app.core.settings import get_settings


def run(cmd: list[str], timeout: int = 5) -> str:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)} :: {result.stderr.strip()}")
    return result.stdout


def service_restart() -> None:
    settings = get_settings()
    run(["systemctl", "restart", settings.service_name], timeout=10)


def service_status() -> Dict[str, Any]:
    settings = get_settings()
    state = run(["systemctl", "is-active", settings.service_name], timeout=3).strip()
    return {"service": settings.service_name, "active": state == "active", "raw": state}


def systemd_brief(unit: str | None = None) -> Dict[str, Any]:
    service = unit or get_settings().service_name
    output = run(
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
        ]
    )
    kv = dict(line.split("=", 1) for line in output.strip().splitlines() if "=" in line)
    return {
        "active": kv.get("ActiveState") == "active",
        "since": kv.get("ActiveEnterTimestamp"),
        "restarts": int(kv.get("NRestarts", "0")),
    }

