"""Filesystem utilities shared across the application."""
from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via tempfile + fsync + rename.

    Creates parent directories as needed.  On failure the original file
    is left untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(path))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise
