from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
from typing import Any, Dict

from app.core.settings import get_settings


def write_env_atomic(new_data: Dict[str, Any]) -> None:
    settings = get_settings()
    env_path = settings.env_path
    lock_path = settings.lock_path
    env_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix="cam-rgb.", suffix=".env", dir=str(env_path.parent)
        )
        with os.fdopen(tmp_fd, "w") as tmp_file:
            for key, value in new_data.items():
                tmp_file.write(f"{key}={value}\n")
        shutil.move(tmp_name, env_path)
        os.chmod(env_path, 0o644)
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def read_env() -> Dict[str, str]:
    env_path = get_settings().env_path
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data

