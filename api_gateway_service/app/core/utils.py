import os
from datetime import datetime, timezone
from pathlib import Path

import httpx


def iso_now() -> str:
    """Compact ISO8601 UTC timestamp (e.g. 2025-11-28T10:15:30Z)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def classify_http_error(exc: Exception) -> str:
    """Classify an httpx/runtime exception into a short reason tag for metrics."""
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_status_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    if isinstance(exc, RuntimeError):
        return "runtime_error"
    return type(exc).__name__


def atomic_write(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    """Crash-safe atomic write for ext4 / RPi SD card.

    Sequence: write tmp → fsync file → rename → fsync parent dir.
    Both file data and directory entry are durable after return.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data.encode(encoding))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
