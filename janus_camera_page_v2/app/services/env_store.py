from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from app.core.settings import get_settings
from app.utils.fs import atomic_write_text

# fcntl is Unix-specific, handle import gracefully
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


def write_env_atomic(new_data: Dict[str, Any]) -> None:
    settings = get_settings()
    env_path = settings.env_path
    lock_path = settings.lock_path

    content = "".join(f"{key}={value}\n" for key, value in new_data.items())

    if HAS_FCNTL:
        # Use "a" mode (not "w") to avoid truncating the lock file before
        # the exclusive lock is obtained — two concurrent openers with "w"
        # could race on different file descriptors to the same inode.
        # The explicit LOCK_UN is omitted: closing the file descriptor
        # releases the advisory lock automatically.
        with open(lock_path, "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            atomic_write_text(env_path, content)
            os.chmod(env_path, 0o644)
    else:
        atomic_write_text(env_path, content)
        os.chmod(env_path, 0o644)


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

